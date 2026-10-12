"""Devices: what is inside and plugged into the PC, and which driver runs each part.

- PCI (graphics card, sound chip, network, storage, ...): `lspci -vmmnnk`, which names the kernel
  driver in use and the modules that could drive it. A device with no driver in use is flagged.
- USB: `lsusb` for names; the drivers bound to each device's interfaces come from sysfs.
- Keyboards, mice and game controllers: /proc/bus/input/devices.
- Motherboard and BIOS: /sys/class/dmi/id (readable without root).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

# PCI class codes (first four hex digits) -> category shown in the Devices tab.
PCI_CATEGORIES = {
    "0300": "Graphics", "0302": "Graphics", "0380": "Graphics",
    "0401": "Audio", "0403": "Audio",
    "0200": "Network", "0280": "Network",
    "0100": "Storage", "0101": "Storage", "0104": "Storage", "0106": "Storage", "0107": "Storage",
    "0108": "Storage", "0180": "Storage",
    "0c03": "USB controllers",
}
CATEGORY_ORDER = ("Graphics", "Audio", "Network", "Storage", "USB controllers", "Keyboards", "Mice",
                  "Game controllers", "USB devices", "Other")
DMI_DIR = "/sys/class/dmi/id"


@dataclass
class Device:
    category: str
    name: str
    vendor: str
    driver: str = ""  # driver in use; "" = none
    modules: list[str] = field(default_factory=list)  # drivers that could handle it
    location: str = ""  # PCI slot, USB bus/device, input path
    ids: str = ""  # vendor:device

    @property
    def has_driver(self) -> bool:
        return bool(self.driver)


def run(argv: list[str]) -> str:
    if shutil.which(argv[0]) is None:
        return ""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _split_bracket(text: str) -> tuple[str, str]:
    """'Advanced Micro Devices, Inc. [AMD/ATI] [1002]' -> ('Advanced Micro Devices, Inc. [AMD/ATI]', '1002')."""
    match = re.match(r"^(.*?)\s*\[([0-9a-fA-F]{4})\]$", text.strip())
    return (match.group(1), match.group(2).lower()) if match else (text.strip(), "")


def parse_lspci(output: str) -> list[Device]:
    """`lspci -vmmnnk`: blank-line separated records of 'Key:<tab>value' lines."""
    devices = []
    for record in output.strip().split("\n\n"):
        fields: dict[str, list[str]] = {}
        for line in record.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields.setdefault(key.strip(), []).append(value.strip())
        if "Slot" not in fields or "Class" not in fields:
            continue
        class_name, class_code = _split_bracket(fields["Class"][0])
        vendor, vendor_id = _split_bracket(fields.get("Vendor", [""])[0])
        device, device_id = _split_bracket(fields.get("Device", [""])[0])
        category = PCI_CATEGORIES.get(class_code, "Other")
        modules = [m for value in fields.get("Module", []) for m in value.split()]
        if device in ("", "Device"):  # lspci has no name for it: say what kind of part it is
            device = class_name
        devices.append(Device(category, device, vendor, fields.get("Driver", [""])[0], modules,
                              fields["Slot"][0], f"{vendor_id}:{device_id}" if vendor_id else ""))
    return devices


def pci_devices() -> list[Device]:
    return parse_lspci(run(["lspci", "-vmmnnk"]))


USB_LINE = re.compile(r"^Bus (\d{3}) Device (\d{3}): ID ([0-9a-f]{4}):([0-9a-f]{4})\s*(.*)$")


def usb_drivers(sysfs: str = "/sys/bus/usb/devices") -> dict[tuple[int, int], list[str]]:
    """(bus, device number) -> drivers bound to the device's interfaces (e.g. ['usbhid'])."""
    drivers: dict[tuple[int, int], list[str]] = {}
    try:
        entries = os.listdir(sysfs)
    except OSError:
        return drivers
    for entry in entries:
        if ":" in entry or entry.startswith("usb"):
            continue  # interfaces are read below; usbN are the root hubs
        base = os.path.join(sysfs, entry)
        try:
            with open(os.path.join(base, "busnum")) as f:
                bus = int(f.read())
            with open(os.path.join(base, "devnum")) as f:
                dev = int(f.read())
        except (OSError, ValueError):
            continue
        found = []
        for interface in sorted(e for e in entries if e.startswith(entry + ":")):
            link = os.path.join(sysfs, interface, "driver")
            if os.path.islink(link):
                name = os.path.basename(os.readlink(link))
                if name not in found:
                    found.append(name)
        drivers[(bus, dev)] = found
    return drivers


def parse_lsusb(output: str, drivers: dict[tuple[int, int], list[str]]) -> list[Device]:
    devices = []
    for line in output.splitlines():
        match = USB_LINE.match(line.strip())
        if not match:
            continue
        bus, dev, vid, pid, description = match.groups()
        if vid == "1d6b":  # Linux Foundation root hubs: part of the USB controller
            continue
        bound = drivers.get((int(bus), int(dev)), [])
        devices.append(Device("USB devices", description or f"{vid}:{pid}", "", ", ".join(bound), [],
                              f"Bus {bus} Device {dev}", f"{vid}:{pid}"))
    return devices


def usb_devices() -> list[Device]:
    return parse_lsusb(run(["lsusb"]), usb_drivers())


def parse_input_devices(text: str) -> list[Device]:
    """Keyboards, mice and game controllers from /proc/bus/input/devices. A keyboard repeats keys
    (EV_REP); a mouse has a 'mouseN' handler; a controller a 'jsN' handler."""
    devices = []
    for block in text.strip().split("\n\n"):
        name, handlers, ev, phys, ids = "", [], 0, "", ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[8:].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line[12:].split()
            elif line.startswith("B: EV="):
                try:
                    ev = int(line[6:].strip(), 16)
                except ValueError:
                    ev = 0
            elif line.startswith("P: Phys="):
                phys = line[8:].strip()
            elif line.startswith("I: "):
                parts = dict(p.split("=", 1) for p in line[3:].split() if "=" in p)
                ids = f"{parts.get('Vendor', '')}:{parts.get('Product', '')}"
        if any(h.startswith("js") for h in handlers):
            category = "Game controllers"
        elif any(h.startswith("mouse") for h in handlers):
            category = "Mice"
        elif "kbd" in handlers and ev & (1 << 0x14):  # EV_REP: real keyboards auto-repeat
            category = "Keyboards"
        else:
            continue  # power buttons, lid switches, speakers, video bus, ...
        driver = "evdev input" if any(h.startswith("event") for h in handlers) else ""
        devices.append(Device(category, name or "Unknown", "", driver, [], phys, ids))
    return devices


def input_devices(path: str = "/proc/bus/input/devices") -> list[Device]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_input_devices(f.read())
    except OSError:
        return []


@dataclass
class Motherboard:
    vendor: str = ""
    model: str = ""
    version: str = ""
    system: str = ""  # PC maker and model (laptops, prebuilt PCs)
    bios_vendor: str = ""
    bios_version: str = ""
    bios_date: str = ""


def _dmi(name: str, base: str) -> str:
    try:
        with open(os.path.join(base, name), encoding="utf-8", errors="replace") as f:
            value = f.read().strip()
    except OSError:
        return ""
    # Placeholders boards ship with instead of real data.
    return "" if value.lower() in ("", "to be filled by o.e.m.", "default string", "not applicable",
                                   "system product name", "system manufacturer", "o.e.m.", "none") else value


def motherboard(base: str = DMI_DIR) -> Motherboard:
    vendor, product = _dmi("sys_vendor", base), _dmi("product_name", base)
    return Motherboard(_dmi("board_vendor", base), _dmi("board_name", base), _dmi("board_version", base),
                       " ".join(x for x in (vendor, product) if x), _dmi("bios_vendor", base),
                       _dmi("bios_version", base), _dmi("bios_date", base))


@dataclass
class Inventory:
    board: Motherboard
    devices: list[Device]

    def by_category(self) -> list[tuple[str, list[Device]]]:
        groups = {c: [d for d in self.devices if d.category == c] for c in CATEGORY_ORDER}
        return [(c, devices) for c, devices in groups.items() if devices]

    @property
    def missing_drivers(self) -> list[Device]:
        # USB controllers and bridges always have drivers; "Other" PCI parts are often fine without.
        return [d for d in self.devices if not d.has_driver and d.category in ("Graphics", "Audio", "Network",
                                                                               "Storage")]


def inventory() -> Inventory:
    return Inventory(motherboard(), pci_devices() + input_devices() + usb_devices())
