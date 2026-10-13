"""Wi-Fi and Ethernet through NetworkManager's command-line client, nmcli.

Ethernet needs no code: NetworkManager creates a DHCP profile for every wired port and brings it
up as soon as a cable is plugged in. This module reads that state, lists Wi-Fi networks, and
joins, disconnects and forgets them. The kiosk user may do this without a password thanks to the
polkit rule in rootfs/etc/polkit-1/rules.d/50-kiosk-power.rules.

Passwords never appear on a command line (where any process could read them from /proc): they
are handed to nmcli in a private temporary file (`connection up ... passwd-file`), and
NetworkManager stores them in its root-only profile for the next boot.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

TIMEOUT = 20  # seconds for quick queries
CONNECT_WAIT = 45  # seconds NetworkManager may take to associate and get an address


class NetworkError(Exception):
    """A failed nmcli call, with a message meant for the user."""


@dataclass
class Device:
    name: str  # wlan0, enp3s0
    type: str  # wifi, ethernet, ...
    state: str  # connected, disconnected, unavailable (ethernet: no cable), ...
    connection: str  # active profile name, or ""

    @property
    def connected(self) -> bool:
        return self.state.startswith("connected")


@dataclass
class WifiNetwork:
    ssid: str
    signal: int  # 0-100
    security: str  # "WPA2", "WPA1 WPA2", "WPA3", "" for open
    in_use: bool = False
    saved: bool = False  # a profile (with its password) exists

    @property
    def open(self) -> bool:
        return not self.security.strip() or self.security.strip() == "--"

    @property
    def enterprise(self) -> bool:
        return "802.1X" in self.security


@dataclass
class NetworkState:
    available: bool = True  # nmcli installed and NetworkManager running
    problem: str = ""  # why not, when not available
    connectivity: str = "unknown"  # full, limited, portal, none, unknown
    wifi_enabled: bool = False
    devices: list[Device] = field(default_factory=list)
    networks: list[WifiNetwork] = field(default_factory=list)
    addresses: dict[str, str] = field(default_factory=dict)  # device -> IPv4 address

    def of_type(self, kind: str) -> list[Device]:
        return [d for d in self.devices if d.type == kind]

    @property
    def wifi_device(self) -> Device | None:
        wifi = self.of_type("wifi")
        return wifi[0] if wifi else None


# --- nmcli plumbing -------------------------------------------------------------------------------


def available() -> bool:
    return shutil.which("nmcli") is not None


def split_terse(line: str) -> list[str]:
    """One line of `nmcli -t` output -> fields. ':' inside a value is escaped as '\\:'."""
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def explain(stderr: str, returncode: int) -> str:
    """nmcli's error -> a short message for the user."""
    text = stderr.strip().removeprefix("Error: ")
    lowered = text.lower()
    if "secrets were required" in lowered or "no-secrets" in lowered:
        return "Wrong password, or the network refused it. Check it and try again."
    if "not authorized" in lowered or "insufficient privileges" in lowered:
        return "Not allowed: polkit refused the network change."
    if "no network with ssid" in lowered:
        return "Network not found. Move closer or scan again."
    if returncode == 8 or "networkmanager is not running" in lowered:
        return "NetworkManager is not running."
    if returncode == 3 or "timeout" in lowered:
        return "The network did not answer in time. Try again."
    if "ip configuration could not be reserved" in lowered:
        return "Connected to the network, but it gave no IP address (DHCP)."
    return text or f"nmcli failed (exit code {returncode})."


def nmcli(*args: str, timeout: int = TIMEOUT) -> str:
    """Run nmcli and return its output; raises NetworkError with a readable message."""
    if not available():
        raise NetworkError("NetworkManager (nmcli) is not installed.")
    # The C locale keeps nmcli's output and error texts in English, which explain() parses.
    env = {**os.environ, "LC_ALL": "C"}
    try:
        result = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise NetworkError("The network did not answer in time. Try again.") from None
    if result.returncode != 0:
        raise NetworkError(explain(result.stderr, result.returncode))
    return result.stdout


def terse(*args: str, timeout: int = TIMEOUT) -> list[list[str]]:
    return [split_terse(line) for line in nmcli("-t", *args, timeout=timeout).splitlines() if line]


# --- reading state --------------------------------------------------------------------------------


def get_value(*args: str) -> str:
    """A single value from `nmcli -g ...` (which escapes ':' like terse mode)."""
    return split_terse(nmcli("-g", *args).strip())[0]


def devices() -> list[Device]:
    """Wired and wireless network cards that NetworkManager controls (unmanaged ones, such as
    virtual links of containers, are left out). "connected (externally)" counts as connected."""
    found = []
    for row in terse("-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"):
        if len(row) >= 4 and row[1] in ("ethernet", "wifi") and row[2] != "unmanaged":
            found.append(Device(row[0], row[1], row[2].split(" ")[0], row[3]))
    return found


def saved_wifi_ssids() -> dict[str, list[str]]:
    """SSID -> names of the saved Wi-Fi profiles for it."""
    saved: dict[str, list[str]] = {}
    for row in terse("-f", "NAME,TYPE", "connection", "show"):
        if len(row) >= 2 and row[1] == "802-11-wireless":
            ssid = get_value("802-11-wireless.ssid", "connection", "show", "id", row[0])
            saved.setdefault(ssid or row[0], []).append(row[0])
    return saved


def wifi_networks(rescan: bool = False, saved: dict[str, list[str]] | None = None) -> list[WifiNetwork]:
    """Visible networks, one entry per SSID (its strongest access point), strongest first."""
    rows = terse("-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes" if rescan else "auto",
                 timeout=TIMEOUT + (15 if rescan else 0))
    saved = saved_wifi_ssids() if saved is None else saved
    best: dict[str, WifiNetwork] = {}
    for row in rows:
        if len(row) < 4 or not row[1]:  # hidden networks have no SSID
            continue
        in_use, ssid, signal, security = row[:4]
        network = WifiNetwork(ssid, int(signal) if signal.isdigit() else 0, "" if security == "--" else security,
                              in_use.strip() == "*", ssid in saved)
        current = best.get(ssid)
        if current is None:
            best[ssid] = network
        else:
            current.in_use = current.in_use or network.in_use
            if network.signal > current.signal:
                current.signal, current.security = network.signal, network.security
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid.lower()))


def ipv4_address(device: str) -> str:
    try:
        value = get_value("IP4.ADDRESS", "device", "show", device)
    except NetworkError:
        return ""
    return value.split("|")[0].strip().split("/")[0]  # "192.168.1.20/24 | 10.0.0.2/8" -> first


def state(rescan: bool = False) -> NetworkState:
    """Everything the Network Settings page shows, in one call (run it off the UI thread)."""
    try:
        status = NetworkState(connectivity=nmcli("networking", "connectivity").strip() or "unknown")
        status.wifi_enabled = nmcli("radio", "wifi").strip() == "enabled"
        status.devices = devices()
        if status.wifi_enabled and status.wifi_device is not None:
            status.networks = wifi_networks(rescan)
        status.addresses = {d.name: ipv4_address(d.name) for d in status.devices if d.connected}
    except NetworkError as error:
        return NetworkState(available=False, problem=str(error))
    return status


# --- changing it ----------------------------------------------------------------------------------


def set_wifi_enabled(on: bool) -> None:
    nmcli("radio", "wifi", "on" if on else "off")


def connect_wifi(ssid: str, password: str | None = None, device: str | None = None,
                 security: str = "WPA2") -> str:
    """Join a Wi-Fi network; returns a confirmation message or raises NetworkError.

    Without a password: an open network, or one with a saved profile. With a password: a fresh
    profile is created (replacing older ones for that SSID, e.g. after a password change) and
    activated with the password supplied through a private file. If activation fails, the new
    profile is removed so a wrong password is never kept."""
    if not password:
        args = ["--wait", str(CONNECT_WAIT), "device", "wifi", "connect", ssid]
        nmcli(*args, *(["ifname", device] if device else []), timeout=CONNECT_WAIT + 15)
        return f"Connected to {ssid}."
    if "802.1X" in security:
        raise NetworkError("Enterprise (802.1X) Wi-Fi needs a username and certificates; not supported here.")
    if len(password) < 8 or len(password) > 63:
        raise NetworkError("A Wi-Fi password is 8 to 63 characters long.")
    for name in saved_wifi_ssids().get(ssid, []):
        nmcli("connection", "delete", "id", name)
    key_mgmt = "sae" if security.strip() == "WPA3" else "wpa-psk"  # WPA2/WPA3 mixed networks accept wpa-psk
    add = ["connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid, "wifi-sec.key-mgmt", key_mgmt]
    nmcli(*add, *(["ifname", device] if device else []))
    fd, secrets = tempfile.mkstemp(prefix="perdix-wifi-", dir=os.environ.get("XDG_RUNTIME_DIR") or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:  # mkstemp: mode 0600, owner only
            f.write(f"802-11-wireless-security.psk:{password}\n")
        nmcli("--wait", str(CONNECT_WAIT), "connection", "up", "id", ssid, "passwd-file", secrets,
              timeout=CONNECT_WAIT + 15)
    except NetworkError:
        try:
            nmcli("connection", "delete", "id", ssid)
        except NetworkError:
            pass
        raise
    finally:
        os.unlink(secrets)
    return f"Connected to {ssid}. The password is saved for next time."


def disconnect(device: str) -> None:
    nmcli("device", "disconnect", device)


def forget(ssid: str) -> int:
    """Delete every saved profile for the SSID; returns how many."""
    names = saved_wifi_ssids().get(ssid, [])
    for name in names:
        nmcli("connection", "delete", "id", name)
    return len(names)
