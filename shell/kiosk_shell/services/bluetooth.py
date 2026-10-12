"""Bluetooth: headphones, speakers, game controllers, mice and keyboards through BlueZ (bluetoothctl).

Every bluetoothctl call has a time limit: when bluetoothd is not running, bluetoothctl waits for it
forever. Pairing registers a NoInputNoOutput agent, the "just works" pairing that headsets and game
controllers use; a device then gets trusted (so it reconnects by itself) and connected. Headphones
and speakers show up as sound outputs in PipeWire on their own. Blueman is there for anything more.

BlueZ's D-Bus policy lets every local user talk to bluetoothd, so the kiosk user needs no extra
permission. Message strings matched here come from BlueZ's client/main.c.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

MAC = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*m|\x01|\x02")
SCAN_SECONDS = 10

# BlueZ's Icon property -> what people call the device.
KINDS = {
    "audio-headset": "Headset", "audio-headphones": "Headphones", "audio-card": "Speaker",
    "input-gaming": "Controller", "input-mouse": "Mouse", "input-keyboard": "Keyboard",
    "input-tablet": "Tablet", "phone": "Phone", "computer": "Computer",
}


class BluetoothError(Exception):
    """A Bluetooth action that failed, with a message meant for the user."""


def clean(text: str) -> str:
    return ANSI.sub("", text)


def run(*args: str, timeout: int = 8) -> str:
    if shutil.which("bluetoothctl") is None:
        raise BluetoothError("Bluetooth tools (bluez-utils) are not installed.")
    try:
        result = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BluetoothError("The Bluetooth service is not answering (is bluetoothd running?).") from None
    return clean(result.stdout + result.stderr)


# --- reading -------------------------------------------------------------------------------------------


@dataclass
class Adapter:
    address: str
    name: str
    powered: bool
    discovering: bool


@dataclass
class Device:
    address: str
    name: str
    kind: str = "Other"
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    battery: int | None = None


def _yes(values: dict[str, str], key: str) -> bool:
    return values.get(key, "no").split()[0] == "yes" if values.get(key) else False


def _properties(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition(": ")
        if sep and key not in values:
            values[key] = value.strip()
    return values


def parse_show(text: str) -> Adapter | None:
    """`bluetoothctl show`; None when there is no adapter."""
    text = clean(text)
    match = re.search(r"Controller ([0-9A-F:]{17})", text, re.I)
    if not match or "No default controller available" in text:
        return None
    values = _properties(text)
    return Adapter(match.group(1).upper(), values.get("Alias") or values.get("Name", ""),
                   _yes(values, "Powered"), _yes(values, "Discovering"))


def parse_devices(text: str) -> list[tuple[str, str]]:
    """`bluetoothctl devices`: 'Device AA:BB:CC:DD:EE:FF Name' lines (also '[NEW] Device ...')."""
    found = []
    for line in clean(text).splitlines():
        match = re.search(r"Device ([0-9A-F:]{17}) (.*)$", line.strip(), re.I)
        if match and MAC.match(match.group(1)):
            found.append((match.group(1).upper(), match.group(2).strip()))
    return found


def parse_info(address: str, text: str) -> Device:
    """`bluetoothctl info <address>`."""
    values = _properties(clean(text))
    battery = re.search(r"\((\d+)\)", values.get("Battery Percentage", ""))
    return Device(address, values.get("Alias") or values.get("Name") or address,
                  KINDS.get(values.get("Icon", ""), "Other"), _yes(values, "Paired"), _yes(values, "Trusted"),
                  _yes(values, "Connected"), int(battery.group(1)) if battery else None)


def adapter() -> Adapter | None:
    return parse_show(run("show"))


def devices() -> list[Device]:
    """Known and recently discovered devices: connected first, then paired, then the rest."""
    found = [parse_info(address, run("info", address)) for address, _name in parse_devices(run("devices"))]
    return sorted(found, key=lambda d: (not d.connected, not d.paired, d.name.lower()))


# --- actions ------------------------------------------------------------------------------------------------


def _check(address: str) -> str:
    if not MAC.match(address):
        raise BluetoothError(f"Not a Bluetooth address: {address}")
    return address.upper()


def _bluez_error(output: str, fallback: str) -> str:
    """'Failed to pair: org.bluez.Error.AuthenticationFailed' -> something people understand."""
    reasons = {
        "AuthenticationFailed": "the device refused the pairing. Put it in pairing mode and try again.",
        "AuthenticationCanceled": "pairing was cancelled on the device.",
        "AuthenticationTimeout": "the device did not answer. Put it in pairing mode and try again.",
        "ConnectionAttemptFailed": "the device is out of reach or switched off.",
        "AlreadyExists": "it is already paired.",
        "InProgress": "another pairing is still running.",
        "NotReady": "Bluetooth is switched off.",
        "DoesNotExist": "the device is no longer visible. Scan again.",
        "br-connection-profile-unavailable": "the device has no profile PERDIX can use (for headphones: is "
                                             "PipeWire running?).",
    }
    for key, reason in reasons.items():
        if key in output:
            return f"{fallback}: {reason}"
    return fallback + "."


def set_powered(on: bool) -> str:
    output = run("power", "on" if on else "off")
    if "succeeded" not in output:
        raise BluetoothError(_bluez_error(output, f"Could not switch Bluetooth {'on' if on else 'off'}"))
    return f"Bluetooth is {'on' if on else 'off'}."


def scan(seconds: int = SCAN_SECONDS) -> list[Device]:
    """Look for devices for a while (bluetoothctl stays connected, so discovery keeps running)."""
    run("--timeout", str(seconds), "scan", "on", timeout=seconds + 8)
    return devices()


def pair(address: str) -> str:
    """Pair, trust (so it reconnects by itself) and connect."""
    address = _check(address)
    output = run("--agent", "NoInputNoOutput", "--timeout", "30", "pair", address, timeout=40)
    if "Pairing successful" not in output and "AlreadyExists" not in output:
        raise BluetoothError(_bluez_error(output, "Pairing failed"))
    run("trust", address)
    return connect(address, paired_now=True)


def connect(address: str, paired_now: bool = False) -> str:
    address = _check(address)
    output = run("--timeout", "20", "connect", address, timeout=30)
    if "Connection successful" not in output:
        message = _bluez_error(output, "Paired, but could not connect" if paired_now else "Could not connect")
        raise BluetoothError(message)
    return "Paired and connected." if paired_now else "Connected."


def disconnect(address: str) -> str:
    run("disconnect", _check(address))
    return "Disconnected."


def remove(address: str) -> str:
    output = run("remove", _check(address))
    if "Device has been removed" not in output and "not available" not in output:
        raise BluetoothError(_bluez_error(output, "Could not forget the device"))
    return "Forgotten. Pair it again to use it."


def open_blueman() -> None:
    if shutil.which("blueman-manager") is None:
        raise BluetoothError("Blueman is not installed.")
    subprocess.Popen(["blueman-manager"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
