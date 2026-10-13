"""Devices: lspci (a real VM listing and a gaming-PC listing in the same format), lsusb with sysfs
drivers, input devices, DMI, and the page."""

from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import devices

app = QApplication.instance() or QApplication([])
DATA = Path(__file__).parent / "data"


def test_lspci_real_vm_output():
    found = devices.parse_lspci((DATA / "lspci-vm.txt").read_text())  # recorded with lspci -vmmnnk
    assert found and all(d.location for d in found)
    storage = [d for d in found if d.category == "Storage"]
    assert storage and all(d.driver == "virtio-pci" for d in storage)
    bridge = next(d for d in found if d.location == "00:00.0")
    assert bridge.name == "Host bridge" and bridge.category == "Other"  # lspci had no device name


def test_lspci_gaming_pc():
    found = {d.location: d for d in devices.parse_lspci((DATA / "lspci-gaming-pc.txt").read_text())}
    gpu = found["03:00.0"]
    assert (gpu.category, gpu.driver, gpu.ids) == ("Graphics", "amdgpu", "1002:744c")
    assert gpu.name.startswith("Navi 31") and gpu.vendor == "Advanced Micro Devices, Inc. [AMD/ATI]"
    nvidia = found["07:00.0"]
    assert not nvidia.has_driver and nvidia.modules == ["nouveau", "nvidia_drm"]
    assert found["03:00.1"].category == "Audio" and found["0d:00.0"].category == "Network"
    assert found["10:00.0"].category == "Storage" and found["10:00.0"].driver == "nvme"


def test_lsusb_with_sysfs_drivers(tmp_path):
    sysfs = tmp_path / "usb"
    for name, bus, dev, drivers in (("1-2", 1, 3, ["usbhid", "usbhid"]), ("1-4", 1, 5, ["snd-usb-audio"]),
                                    ("3-1", 3, 2, [])):
        (sysfs / name).mkdir(parents=True)
        (sysfs / name / "busnum").write_text(f"{bus}\n")
        (sysfs / name / "devnum").write_text(f"{dev}\n")
        for i, driver in enumerate(drivers):
            interface = sysfs / f"{name}:1.{i}"
            interface.mkdir()
            (tmp_path / "drivers" / driver).mkdir(parents=True, exist_ok=True)
            (interface / "driver").symlink_to(tmp_path / "drivers" / driver)
    drivers = devices.usb_drivers(str(sysfs))
    assert drivers == {(1, 3): ["usbhid"], (1, 5): ["snd-usb-audio"], (3, 2): []}
    lsusb = ("Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
             "Bus 001 Device 003: ID 046d:c539 Logitech, Inc. Lightspeed Receiver\n"
             "Bus 001 Device 005: ID 1532:0527 Razer USA, Ltd Kraken Ultimate\n"
             "Bus 003 Device 002: ID 0bda:8153 Realtek Semiconductor Corp. RTL8153 Gigabit Ethernet Adapter\n")
    found = devices.parse_lsusb(lsusb, drivers)
    assert [(d.name, d.driver, d.ids) for d in found] == [
        ("Logitech, Inc. Lightspeed Receiver", "usbhid", "046d:c539"),
        ("Razer USA, Ltd Kraken Ultimate", "snd-usb-audio", "1532:0527"),
        ("Realtek Semiconductor Corp. RTL8153 Gigabit Ethernet Adapter", "", "0bda:8153"),
    ]


INPUT = """I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=PNP0C0C/button/input0
H: Handlers=kbd event0
B: PROP=0
B: EV=3

I: Bus=0003 Vendor=046d Product=c539 Version=0111
N: Name="Logitech G Pro Keyboard"
P: Phys=usb-0000:0e:00.3-2/input0
H: Handlers=sysrq kbd leds event3
B: PROP=0
B: EV=120013

I: Bus=0003 Vendor=046d Product=c539 Version=0111
N: Name="Logitech G Pro X Superlight"
P: Phys=usb-0000:0e:00.3-2/input1
H: Handlers=mouse0 event4
B: PROP=0
B: EV=17

I: Bus=0005 Vendor=054c Product=0ce6 Version=8100
N: Name="DualSense Wireless Controller"
P: Phys=e8:48:b8:c8:20:00
H: Handlers=event5 js0
B: PROP=0
B: EV=20000b
"""


def test_input_devices():
    found = devices.parse_input_devices(INPUT)
    assert [(d.category, d.name, d.ids) for d in found] == [
        ("Keyboards", "Logitech G Pro Keyboard", "046d:c539"),
        ("Mice", "Logitech G Pro X Superlight", "046d:c539"),
        ("Game controllers", "DualSense Wireless Controller", "054c:0ce6"),
    ]
    assert all(d.driver == "evdev input" for d in found)


def test_motherboard(tmp_path):
    for name, value in (("board_vendor", "ASUSTeK COMPUTER INC."), ("board_name", "ROG STRIX B650E-F GAMING WIFI"),
                        ("board_version", "Rev 1.xx"), ("sys_vendor", "System manufacturer"),
                        ("product_name", "System Product Name"), ("bios_vendor", "American Megatrends Inc."),
                        ("bios_version", "3024"), ("bios_date", "08/02/2024")):
        (tmp_path / name).write_text(value + "\n")
    board = devices.motherboard(str(tmp_path))
    assert (board.vendor, board.model, board.bios_version, board.bios_date) == (
        "ASUSTeK COMPUTER INC.", "ROG STRIX B650E-F GAMING WIFI", "3024", "08/02/2024")
    assert board.system == ""  # placeholder text from a self-built PC is dropped
    assert devices.motherboard(str(tmp_path / "missing")) == devices.Motherboard()


def gaming_inventory():
    found = devices.parse_lspci((DATA / "lspci-gaming-pc.txt").read_text()) + devices.parse_input_devices(INPUT)
    return devices.Inventory(devices.Motherboard("ASUSTeK", "ROG STRIX B650E-F", bios_version="3024",
                                                 bios_date="08/02/2024"), found)


def test_inventory_groups_and_missing_drivers():
    inventory = gaming_inventory()
    assert [c for c, _ in inventory.by_category()] == ["Graphics", "Audio", "Network", "Storage", "Keyboards",
                                                      "Mice", "Game controllers", "Other"]
    assert [d.name for d in inventory.missing_drivers] == ["AD104 [GeForce RTX 4070]"]


@pytest.fixture
def page():
    from kiosk_shell.perdix_hub import DevicesPage
    return DevicesPage()


def test_page_tree_and_warning(page):
    page.show_inventory(gaming_inventory())
    groups = [page.tree.topLevelItem(i).text(0) for i in range(page.tree.topLevelItemCount())]
    assert groups[:2] == ["Graphics  (2)", "Audio  (2)"]
    graphics = page.tree.topLevelItem(0)
    assert graphics.child(0).text(1) == "amdgpu"
    assert graphics.child(1).text(1) == "none (possible: nouveau, nvidia_drm)"
    assert not page.warning.isHidden() and "GeForce RTX 4070" in page.warning.text()
    assert page.board_value.value.text() == "ASUSTeK ROG STRIX B650E-F"
    assert page.bios_value.value.text() == "3024 (08/02/2024)"
    assert page.tree.topLevelItem(len(groups) - 1).isExpanded() is False  # "Other" stays folded


def test_page_reads_this_machine(page):
    page.refresh()
    page._worker.wait(10000)
    app.processEvents()
    assert page.inventory is not None and page.status.text().endswith(".")


def test_devices_is_in_the_system_group():
    from kiosk_shell.perdix_hub import NAV
    assert ("devices", "chip", "Devices", "Hardware · drivers · USB", "System") in NAV
