"""Bluetooth: parsers fed bluetoothctl's own output formats (BlueZ 5.7x), the actions against a fake
bluetoothctl, the time limit when bluetoothd does not answer, and the page."""

import subprocess

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import bluetooth

app = QApplication.instance() or QApplication([])

SHOW = """\x1b[0;94m[bluetooth]\x1b[0m# Controller 9C:B6:D0:12:34:56 (public)
\tManufacturer: 0x0002 (2)
\tVersion: 0x0c (12)
\tName: perdix
\tAlias: PERDIX Gaming PC
\tClass: 0x007c010c (8126732)
\tPowered: yes
\tPowerState: on
\tDiscoverable: no
\tPairable: yes
\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)
\tDiscovering: no
"""
DEVICES = """Device 4C:B9:9B:11:22:33 DualSense Wireless Controller
\x1b[0;92m[NEW]\x1b[0m Device 00:1B:66:AA:BB:CC WH-1000XM4
Device 7A:2F:5E:01:02:03 7A-2F-5E-01-02-03
Device ZZ:ZZ:ZZ:ZZ:ZZ:ZZ broken line
"""
INFO = {
    "4C:B9:9B:11:22:33": """Device 4C:B9:9B:11:22:33 (public)
\tName: DualSense Wireless Controller
\tAlias: DualSense Wireless Controller
\tClass: 0x00002508 (9480)
\tIcon: input-gaming
\tPaired: yes
\tBonded: yes
\tTrusted: yes
\tBlocked: no
\tConnected: no
\tLegacyPairing: no
""",
    "00:1B:66:AA:BB:CC": """Device 00:1B:66:AA:BB:CC (public)
\tName: WH-1000XM4
\tAlias: WH-1000XM4
\tIcon: audio-headset
\tPaired: yes
\tTrusted: yes
\tConnected: yes
\tBattery Percentage: 0x46 (70)
""",
    "7A:2F:5E:01:02:03": """Device 7A:2F:5E:01:02:03 (random)
\tAlias: 7A-2F-5E-01-02-03
\tPaired: no
\tTrusted: no
\tConnected: no
\tRSSI: 0xffffffb5 (-75)
""",
}


def test_parse_show():
    a = bluetooth.parse_show(SHOW)
    assert (a.address, a.name, a.powered, a.discovering) == ("9C:B6:D0:12:34:56", "PERDIX Gaming PC", True, False)
    assert bluetooth.parse_show("No default controller available\n") is None
    assert bluetooth.parse_show("Unable to open mgmt_socket\n") is None


def test_parse_devices_strips_colours_and_skips_garbage():
    assert bluetooth.parse_devices(DEVICES) == [
        ("4C:B9:9B:11:22:33", "DualSense Wireless Controller"), ("00:1B:66:AA:BB:CC", "WH-1000XM4"),
        ("7A:2F:5E:01:02:03", "7A-2F-5E-01-02-03")]


def test_parse_info():
    pad = bluetooth.parse_info("4C:B9:9B:11:22:33", INFO["4C:B9:9B:11:22:33"])
    assert (pad.kind, pad.paired, pad.trusted, pad.connected, pad.battery) == ("Controller", True, True, False, None)
    phones = bluetooth.parse_info("00:1B:66:AA:BB:CC", INFO["00:1B:66:AA:BB:CC"])
    assert (phones.name, phones.kind, phones.connected, phones.battery) == ("WH-1000XM4", "Headset", True, 70)
    unknown = bluetooth.parse_info("7A:2F:5E:01:02:03", INFO["7A:2F:5E:01:02:03"])
    assert (unknown.kind, unknown.paired) == ("Other", False)


# --- a fake bluetoothctl -----------------------------------------------------------------------------------


class FakeCtl:
    """Answers like bluetoothctl; `replies` overrides a command's answer."""

    def __init__(self, **replies):
        self.calls = []
        self.replies = replies

    def __call__(self, *args, timeout=8):
        self.calls.append(args)
        command = [a for a in args if not a.startswith("--") and not a.isdigit() and a != "NoInputNoOutput"]
        name = command[0]
        if name in self.replies:
            return self.replies[name]
        if name == "show":
            return SHOW
        if name == "devices":
            return DEVICES
        if name == "info":
            return INFO[command[1]]
        return ""


@pytest.fixture
def ctl(monkeypatch):
    fake = FakeCtl()
    monkeypatch.setattr(bluetooth, "run", fake)
    return fake


def test_devices_connected_first_then_paired(ctl):
    assert [d.name for d in bluetooth.devices()] == ["WH-1000XM4", "DualSense Wireless Controller",
                                                      "7A-2F-5E-01-02-03"]


def test_pair_trusts_and_connects(ctl):
    ctl.replies.update(pair="Attempting to pair with 7A:2F:5E:01:02:03\n[CHG] Device 7A:2F:5E:01:02:03 "
                            "Paired: yes\nPairing successful\n",
                       trust="Changing 7A:2F:5E:01:02:03 trust succeeded\n",
                       connect="Attempting to connect to 7A:2F:5E:01:02:03\nConnection successful\n")
    assert bluetooth.pair("7a:2f:5e:01:02:03") == "Paired and connected."
    names = [next(a for a in c if not a.startswith("--") and not a.isdigit() and a != "NoInputNoOutput")
             for c in ctl.calls]
    assert names == ["pair", "trust", "connect"]
    assert ctl.calls[0][:2] == ("--agent", "NoInputNoOutput") and ctl.calls[0][-1] == "7A:2F:5E:01:02:03"


def test_pair_failure_explains(ctl):
    ctl.replies["pair"] = "Attempting to pair with 7A:2F:5E:01:02:03\nFailed to pair: org.bluez.Error.AuthenticationFailed\n"
    with pytest.raises(bluetooth.BluetoothError, match="Pairing failed: the device refused the pairing"):
        bluetooth.pair("7A:2F:5E:01:02:03")
    ctl.replies["pair"] = "Failed to pair: org.bluez.Error.AlreadyExists\n"
    ctl.replies["connect"] = "Failed to connect: org.bluez.Error.Failed br-connection-profile-unavailable\n"
    with pytest.raises(bluetooth.BluetoothError, match="Paired, but could not connect: .*PipeWire"):
        bluetooth.pair("7A:2F:5E:01:02:03")


def test_bad_addresses_never_reach_bluetoothctl(ctl):
    for bad in ("", "AA:BB", "AA:BB:CC:DD:EE:FF; reboot", "--help"):
        with pytest.raises(bluetooth.BluetoothError, match="Not a Bluetooth address"):
            bluetooth.connect(bad)
    assert ctl.calls == []


def test_power_remove_and_connect_messages(ctl):
    ctl.replies["power"] = "Changing power on succeeded\n"
    assert bluetooth.set_powered(True) == "Bluetooth is on."
    ctl.replies["power"] = "Failed to set power on: org.bluez.Error.Failed\n"
    with pytest.raises(bluetooth.BluetoothError, match="Could not switch Bluetooth on"):
        bluetooth.set_powered(True)
    ctl.replies["remove"] = "[DEL] Device 4C:B9:9B:11:22:33 DualSense\nDevice has been removed\n"
    assert bluetooth.remove("4C:B9:9B:11:22:33").startswith("Forgotten")
    ctl.replies["connect"] = "Failed to connect: org.bluez.Error.Failed ConnectionAttemptFailed\n"
    with pytest.raises(bluetooth.BluetoothError, match="out of reach or switched off"):
        bluetooth.connect("4C:B9:9B:11:22:33")


def test_scan_runs_discovery_with_a_time_limit(ctl):
    found = bluetooth.scan(3)
    assert ctl.calls[0] == ("--timeout", "3", "scan", "on") and len(found) == 3


def test_run_gives_up_when_bluetoothd_does_not_answer(monkeypatch):
    monkeypatch.setattr(bluetooth.shutil, "which", lambda name: "/usr/bin/" + name)

    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(bluetooth.subprocess, "run", hang)
    with pytest.raises(bluetooth.BluetoothError, match="not answering"):
        bluetooth.run("show")


def test_run_without_bluez(monkeypatch):
    monkeypatch.setattr(bluetooth.shutil, "which", lambda name: None)
    with pytest.raises(bluetooth.BluetoothError, match="bluez-utils"):
        bluetooth.adapter()


# --- the page -----------------------------------------------------------------------------------------------


@pytest.fixture
def page(ctl):
    from kiosk_shell.perdix_hub import BluetoothPage
    page = BluetoothPage()
    yield page
    page.shutdown()


def settle(page):
    if page._worker is not None:
        page._worker.wait(5000)
    for _ in range(5):
        app.processEvents()
    if page._worker is not None:
        page._worker.wait(5000)
    app.processEvents()


def row_of(page, name):
    return next(r for r in range(page.table.rowCount()) if page.table.item(r, 0).text() == name)


def test_page_lists_devices(page):
    page.refresh()
    settle(page)
    assert page.adapter_value.value.text() == "PERDIX Gaming PC" and page.connected_value.value.text() == "1"
    assert page.power.isChecked()
    r = row_of(page, "WH-1000XM4")
    assert [page.table.item(r, c).text() for c in range(4)] == ["WH-1000XM4", "Headset", "Connected", "70 %"]


def test_page_button_follows_the_device_state(page):
    page.refresh()
    settle(page)
    expected = {"WH-1000XM4": "Disconnect", "DualSense Wireless Controller": "Connect",
                "7A-2F-5E-01-02-03": "Pair and connect"}
    for name, label in expected.items():
        page.table.selectRow(row_of(page, name))
        assert page.action_button.text() == label
        assert page.forget_button.isEnabled() == (label != "Pair and connect")


def test_page_pairs_the_selected_device(page, ctl):
    ctl.replies.update(pair="Pairing successful\n", connect="Connection successful\n")
    page.refresh()
    settle(page)
    page.table.selectRow(row_of(page, "7A-2F-5E-01-02-03"))
    page.action_button.click()
    settle(page)
    assert page.status.text() == "Paired and connected."
    assert any("pair" in call for call in ctl.calls)


def test_page_without_adapter(monkeypatch, page, ctl):
    ctl.replies["show"] = "No default controller available\n"
    page.refresh()
    settle(page)
    assert "No Bluetooth adapter" in page.status.text() and not page.scan_button.isEnabled()
    assert page.table.rowCount() == 0 and not page.power.isEnabled()


def test_bluetooth_is_in_network_and_sound():
    from kiosk_shell.perdix_hub import NAV
    assert ("bluetooth", "bluetooth", "Bluetooth", "Headsets · controllers", "Network & Sound") in NAV
