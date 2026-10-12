"""Network Settings: the nmcli service (with a fake nmcli) and the 06 page offscreen."""

import os
import stat
import subprocess

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import network

app = QApplication.instance() or QApplication([])

DEVICES = ("wlan0:wifi:connected:Home\nenp3s0:ethernet:unavailable:\nlo:loopback:connected (externally):lo\n"
           "veth0:ethernet:unmanaged:\n")
WIFI_LIST = (
    "*:Home:70:WPA2\n"
    " :Home:82:WPA2\n"  # second access point of the same network: stronger
    " :Cafe\\: Free:40:--\n"  # ':' in an SSID is escaped by nmcli
    " ::90:WPA2\n"  # hidden network: no SSID
    " :Office:55:WPA2 802.1X\n"
    " :Neighbour:30:WPA3\n"
)
CONNECTIONS = "Home:802-11-wireless\nWired connection 1:802-3-ethernet\n"


class FakeNmcli:
    """Stands in for subprocess.run(["nmcli", ...]). Answers by the arguments after the options."""

    def __init__(self, monkeypatch, overrides=None):
        self.calls, self.secrets = [], []
        self.answers = {
            ("networking", "connectivity"): "full\n",
            ("radio", "wifi"): "enabled\n",
            ("-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"): DEVICES,
            ("-f", "NAME,TYPE", "connection", "show"): CONNECTIONS,
            ("802-11-wireless.ssid", "connection", "show", "id", "Home"): "Home\n",
            ("IP4.ADDRESS", "device", "show", "wlan0"): "192.168.1.20/24\n",
        }
        self.answers.update(overrides or {})
        monkeypatch.setattr(network.shutil, "which", lambda name: "/usr/bin/nmcli")
        monkeypatch.setattr(network.subprocess, "run", self.run)

    def run(self, cmd, **kwargs):
        assert cmd[0] == "nmcli" and kwargs["env"]["LC_ALL"] == "C"
        self.calls.append(cmd[1:])
        args = [a for a in cmd[1:] if a not in ("-t", "-g")]
        if "passwd-file" in args:
            path = args[args.index("passwd-file") + 1]
            self.secrets.append((open(path).read(), stat.S_IMODE(os.stat(path).st_mode)))
        if args[:4] == ["-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi"]:
            key = ("wifi-list",)
        elif args[:1] == ["--wait"]:
            key = tuple(args[2:])
        else:
            key = tuple(args)
        answer = self.answers.get(key, "")
        if isinstance(answer, tuple):  # (returncode, stderr)
            return subprocess.CompletedProcess(cmd, answer[0], "", answer[1])
        return subprocess.CompletedProcess(cmd, 0, answer, "")


@pytest.fixture
def nm(monkeypatch):
    return FakeNmcli(monkeypatch, {("wifi-list",): WIFI_LIST})


# --- service --------------------------------------------------------------------------------------


def test_split_terse():
    assert network.split_terse("a:b\\:c:") == ["a", "b:c", ""]
    assert network.split_terse("back\\\\slash:x") == ["back\\slash", "x"]


@pytest.mark.parametrize("stderr, code, expected", [
    ("Error: Connection activation failed: Secrets were required, but not provided.", 4, "Wrong password"),
    ("Error: Not authorized to control networking.", 1, "Not allowed"),
    ("Error: No network with SSID 'X' found.", 10, "Network not found"),
    ("Error: NetworkManager is not running.", 8, "NetworkManager is not running."),
    ("Error: Timeout expired (45 seconds)", 3, "did not answer in time"),
    ("", 2, "nmcli failed (exit code 2)."),
])
def test_explain(stderr, code, expected):
    assert expected in network.explain(stderr, code)


def test_devices_skip_loopback_and_unmanaged(nm):
    assert network.devices() == [
        network.Device("wlan0", "wifi", "connected", "Home"),
        network.Device("enp3s0", "ethernet", "unavailable", ""),
    ]


def test_wifi_networks(nm):
    networks = network.wifi_networks()
    assert [n.ssid for n in networks] == ["Home", "Office", "Cafe: Free", "Neighbour"]
    home = networks[0]
    assert home.in_use and home.saved and home.signal == 82  # strongest access point
    cafe = networks[2]
    assert cafe.open and not cafe.saved and cafe.security == ""
    assert networks[1].enterprise and not networks[3].open
    assert ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "auto"] in nm.calls


def test_state(nm):
    state = network.state()
    assert state.available and state.connectivity == "full" and state.wifi_enabled
    assert state.wifi_device.name == "wlan0" and [d.name for d in state.of_type("ethernet")] == ["enp3s0"]
    assert state.addresses == {"wlan0": "192.168.1.20"}
    assert len(state.networks) == 4


def test_state_without_networkmanager(monkeypatch):
    monkeypatch.setattr(network.shutil, "which", lambda name: None)
    state = network.state()
    assert not state.available and state.problem == "NetworkManager (nmcli) is not installed."
    FakeNmcli(monkeypatch, {("networking", "connectivity"): (8, "Error: NetworkManager is not running.")})
    assert network.state().problem == "NetworkManager is not running."


def test_connect_with_password_never_puts_it_on_a_command_line(nm, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    message = network.connect_wifi("Home", "correct horse", "wlan0", "WPA2")
    assert message == "Connected to Home. The password is saved for next time."
    assert ["connection", "delete", "id", "Home"] in nm.calls  # the old profile is replaced
    assert ["connection", "add", "type", "wifi", "con-name", "Home", "ssid", "Home",
            "wifi-sec.key-mgmt", "wpa-psk", "ifname", "wlan0"] in nm.calls
    assert nm.secrets == [("802-11-wireless-security.psk:correct horse\n", 0o600)]
    assert not any("correct horse" in arg for call in nm.calls for arg in call)
    assert list(tmp_path.iterdir()) == []  # the password file is gone


def test_wrong_password_removes_the_new_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    nm = FakeNmcli(monkeypatch, {("-f", "NAME,TYPE", "connection", "show"): ""})  # nothing saved yet
    real_run = nm.run

    def failing_up(cmd, **kwargs):
        result = real_run(cmd, **kwargs)
        if "up" in cmd:
            return subprocess.CompletedProcess(cmd, 4, "", "Error: Connection activation failed: "
                                               "Secrets were required, but not provided.")
        return result
    monkeypatch.setattr(network.subprocess, "run", failing_up)
    with pytest.raises(network.NetworkError, match="Wrong password"):
        network.connect_wifi("Cafe", "wrongpass", None, "WPA3")
    assert ["connection", "add", "type", "wifi", "con-name", "Cafe", "ssid", "Cafe",
            "wifi-sec.key-mgmt", "sae"] in nm.calls
    assert nm.calls[-1] == ["connection", "delete", "id", "Cafe"]
    assert list(tmp_path.iterdir()) == []


def test_connect_without_password(nm):
    assert network.connect_wifi("Cafe: Free", None, "wlan0") == "Connected to Cafe: Free."
    assert nm.calls[-1] == ["--wait", "45", "device", "wifi", "connect", "Cafe: Free", "ifname", "wlan0"]


def test_connect_rejects_bad_input(nm):
    with pytest.raises(network.NetworkError, match="8 to 63"):
        network.connect_wifi("Home", "short")
    with pytest.raises(network.NetworkError, match="802.1X"):
        network.connect_wifi("Office", "longenough", security="WPA2 802.1X")
    assert nm.calls == []


def test_forget_and_disconnect(nm):
    assert network.forget("Home") == 1
    assert nm.calls[-1] == ["connection", "delete", "id", "Home"]
    network.disconnect("wlan0")
    network.set_wifi_enabled(False)
    assert nm.calls[-2:] == [["device", "disconnect", "wlan0"], ["radio", "wifi", "off"]]


# --- the 06 page ----------------------------------------------------------------------------------


def settle(page):
    for _ in range(6):
        if page._worker is not None:
            page._worker.wait(10000)
        app.processEvents()


def sample_state(**changes):
    state = network.NetworkState(
        connectivity="full", wifi_enabled=True,
        devices=[network.Device("wlan0", "wifi", "connected", "Home"),
                 network.Device("enp3s0", "ethernet", "connected", "Wired connection 1")],
        networks=[network.WifiNetwork("Home", 82, "WPA2", in_use=True, saved=True),
                  network.WifiNetwork("Cafe", 40, ""),
                  network.WifiNetwork("Neighbour", 30, "WPA3"),
                  network.WifiNetwork("Office", 55, "WPA2 802.1X")],
        addresses={"wlan0": "192.168.1.20", "enp3s0": "10.0.0.5"})
    for key, value in changes.items():
        setattr(state, key, value)
    return state


@pytest.fixture
def page():
    from kiosk_shell.perdix_hub import WifiPage
    return WifiPage()


def select(page, ssid):
    page.table.selectRow([n.ssid for n in page.networks].index(ssid))


def test_page_shows_state(page):
    page.show_state(sample_state())
    assert page.internet_value.value.text() == "ONLINE"
    assert page.ethernet_value.value.text() == "CONNECTED"
    assert page.wifi_value.value.text() == "HOME"
    assert page.address_value.value.text() == "10.0.0.5"  # the cable comes first
    assert page.wired_list.text() == "enp3s0: connected · 10.0.0.5"
    rows = [[page.table.item(r, c).text() for c in range(4)] for r in range(page.table.rowCount())]
    assert rows[0] == ["Home", "▂▄▆█  82%", "WPA2", "Connected"]
    assert rows[1][1:3] == ["▂▄··  40%", "Open"]
    assert page.wifi_switch.isChecked() and page.disconnect_button.isEnabled()


def test_password_field_follows_the_selection(page):
    page.show_state(sample_state())
    select(page, "Cafe")
    assert not page.password.isEnabled() and "Open network" in page.password.placeholderText()
    select(page, "Neighbour")
    assert page.password.isEnabled() and page.password.placeholderText() == "Password for Neighbour"
    select(page, "Office")
    assert not page.connect_button.isEnabled()  # 802.1X
    select(page, "Home")
    assert not page.connect_button.isEnabled() and page.forget_button.isEnabled()


def test_enter_in_password_field_connects(page, monkeypatch):
    calls = []
    monkeypatch.setattr(network, "connect_wifi", lambda *a: calls.append(a) or "Connected to Neighbour.")
    monkeypatch.setattr(network, "state", lambda rescan=False: sample_state())
    page.show_state(sample_state())
    select(page, "Neighbour")
    page.password.setText("hunter2hunter2")
    page.password.returnPressed.emit()
    settle(page)
    assert calls == [("Neighbour", "hunter2hunter2", "wlan0", "WPA3")]
    assert page.password.text() == ""  # never left in the field
    assert page.status.text() == "Connected to Neighbour."


def test_connect_needs_a_password_for_new_secure_networks(page, monkeypatch):
    monkeypatch.setattr(network, "connect_wifi", lambda *a: pytest.fail("connected without password"))
    page.show_state(sample_state())
    select(page, "Neighbour")
    page.connect_clicked()
    assert page.status.text() == "Enter the password for Neighbour."


def test_open_network_connects_without_password(page, monkeypatch):
    calls = []
    monkeypatch.setattr(network, "connect_wifi", lambda *a: calls.append(a) or "Connected to Cafe.")
    monkeypatch.setattr(network, "state", lambda rescan=False: sample_state())
    page.show_state(sample_state())
    select(page, "Cafe")
    page.connect_clicked()
    settle(page)
    assert calls == [("Cafe", None, "wlan0", "")]


def test_connection_error_is_shown(page, monkeypatch):
    def fail(*a):
        raise network.NetworkError("Wrong password, or the network refused it. Check it and try again.")
    monkeypatch.setattr(network, "connect_wifi", fail)
    monkeypatch.setattr(network, "state", lambda rescan=False: sample_state())
    page.show_state(sample_state())
    select(page, "Neighbour")
    page.password.setText("wrongwrong")
    page.connect_clicked()
    settle(page)
    assert page.status.text().startswith("Wrong password") and page.status.objectName() == "StatusError"


def test_unplugged_cable_and_wifi_off(page):
    page.show_state(sample_state(
        wifi_enabled=False, networks=[], addresses={},
        devices=[network.Device("wlan0", "wifi", "disconnected", ""),
                 network.Device("enp3s0", "ethernet", "unavailable", "")]))
    assert page.ethernet_value.value.text() == "NO CABLE" and page.wifi_value.value.text() == "OFF"
    assert page.wired_list.text() == "enp3s0: no cable plugged in"
    assert page.status.text() == "Wi-Fi is off. Turn it on to see networks."
    assert not page.wifi_switch.isChecked() and not page.scan_button.isEnabled()


def test_no_wifi_adapter(page):
    page.show_state(sample_state(devices=[network.Device("enp3s0", "ethernet", "connected", "Wired")], networks=[]))
    assert page.wifi_value.value.text() == "NO ADAPTER"
    assert not page.wifi_switch.isChecked() and not page.wifi_switch.isEnabled()
    assert page.status.text().startswith("No Wi-Fi adapter found.")


def test_networkmanager_missing(page):
    page.show_state(network.NetworkState(available=False, problem="NetworkManager (nmcli) is not installed."))
    assert page.status.text() == "NetworkManager (nmcli) is not installed."
    assert page.table.rowCount() == 0 and not page.connect_button.isEnabled()


def test_background_refresh_runs_while_visible(page, monkeypatch):
    calls = []
    monkeypatch.setattr(network, "state", lambda rescan=False: calls.append(rescan) or sample_state())
    page.show()
    settle(page)
    assert calls == [True] and page._timer.isActive()  # first visit: real scan
    page.refresh()
    settle(page)
    assert calls == [True, False]
    page.hide()
    assert not page._timer.isActive()


def test_hub_menu():
    from kiosk_shell.perdix_hub import HubWindow
    assert [m[2] for m in HubWindow.MODULES[4:]] == ["Zero-Ping", "Network Settings", "Kılavuz"]
