"""Region & Language: parsers on output recorded here (timedatectl/localectl against the real
timedated/localed services, setxkbmap on Xvfb), the commands sent, and the page."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import region

app = QApplication.instance() or QApplication([])
DATA = Path(__file__).parent / "data"

ARCH_LOCALECTL = """System Locale: LANG=tr_TR.UTF-8
    VC Keymap: trq
   X11 Layout: us,tr
    X11 Model: pc105
  X11 Variant: ,f
  X11 Options: grp:alt_shift_toggle
"""


def test_timedatectl_show_recorded():
    t = region.parse_timedatectl_show((DATA / "timedatectl-show.txt").read_text())
    assert (t.timezone, t.ntp, t.ntp_synced, t.can_ntp) == ("Europe/Istanbul", False, False, False)
    assert t.time.endswith("+03")


def test_localectl_status():
    unset = region.parse_localectl_status((DATA / "localectl-status.txt").read_text())  # recorded
    assert (unset.lang, unset.layouts, unset.vc_keymap) == ("C.UTF-8", [], "")
    arch = region.parse_localectl_status(ARCH_LOCALECTL)
    assert (arch.lang, arch.layouts, arch.variants, arch.options, arch.vc_keymap) == (
        "tr_TR.UTF-8", ["us", "tr"], ["", "f"], "grp:alt_shift_toggle", "trq")


def test_setxkbmap_query_recorded():
    q = region.parse_setxkbmap_query((DATA / "setxkbmap-query.txt").read_text())
    assert (q.layouts, q.variants, q.options) == (["us", "tr"], ["", "f"], "grp:alt_shift_toggle")


def test_names():
    assert region.keyboard_name("tr", "f") == "Türkçe F" and region.keyboard_name("tr") == "Türkçe Q"
    assert region.keyboard_name("dvorak") == "dvorak"
    assert region.language_name("tr_TR.UTF-8") == "Türkçe" and region.language_name("xx_YY.UTF-8") == "xx_YY"


class Calls(list):
    failing: set


@pytest.fixture
def commands(monkeypatch):
    calls = Calls()
    failing = set()

    def fake(*argv, timeout=15):
        calls.append(list(argv))
        if argv[0] in failing:
            raise region.RegionError("Setting X11 and console keymaps is not supported in Debian.")
        return ""
    monkeypatch.setattr(region, "run", fake)
    monkeypatch.setattr(region.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("DISPLAY", ":0")
    calls.failing = failing
    return calls


def test_two_keyboard_layouts(commands):
    message = region.set_keyboard(("us", ""), ("tr", "f"))
    assert commands == [
        ["setxkbmap", "-layout", "us,tr", "-variant", ",f", "-option", "", "-option", "grp:alt_shift_toggle"],
        ["localectl", "set-x11-keymap", "us,tr", "pc105", ",f", "grp:alt_shift_toggle"],
    ]
    assert message == "Keyboard: English (US) + Türkçe F (Alt+Shift switches)."


def test_one_keyboard_layout_clears_options(commands):
    region.set_keyboard(("tr", ""), ("tr", ""))  # the same twice is one layout
    assert commands == [["setxkbmap", "-layout", "tr", "-variant", "", "-option", ""],
                        ["localectl", "set-x11-keymap", "tr", "pc105", "", ""]]


def test_keyboard_saved_for_session_only(commands):
    commands.failing.add("localectl")
    assert region.set_keyboard(("tr", "f")).endswith("for this session only (could not save it for the next boot).")


def test_keyboard_rejects_odd_names(commands):
    with pytest.raises(region.RegionError, match="Unknown keyboard layout"):
        region.set_keyboard(("us;rm -rf", ""))
    assert commands == []


def test_timezone_and_language(commands):
    assert region.set_timezone("America/New_York") == "Time zone set to America/New York."
    with pytest.raises(region.RegionError, match="Unknown time zone"):
        region.set_timezone("../../etc/passwd")
    assert region.set_language("tr_TR.UTF-8", ["en_US.UTF-8", "tr_TR.UTF-8"]).startswith("Language set to Türkçe.")
    with pytest.raises(region.RegionError, match="not installed"):
        region.set_language("ja_JP.UTF-8", ["en_US.UTF-8"])
    assert commands == [["timedatectl", "set-timezone", "America/New_York"],
                        ["localectl", "set-locale", "LANG=tr_TR.UTF-8"]]


def test_error_messages(monkeypatch):
    monkeypatch.setattr(region.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(region.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(
        argv, 1, "", "Failed to set time zone: Invalid or not installed time zone 'Mars/Olympus'\n"))
    with pytest.raises(region.RegionError) as error:
        region.run("timedatectl", "set-timezone", "Mars/Olympus")
    assert str(error.value) == "Invalid or not installed time zone 'Mars/Olympus'"  # the real message, recorded
    monkeypatch.setattr(region.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(
        argv, 1, "", "Failed to connect to bus: Connection refused\n"))
    with pytest.raises(region.RegionError, match="settings service is not running"):
        region.run("localectl", "status")


def test_keyboard_on_a_real_x_server(monkeypatch):
    if shutil.which("Xvfb") is None or shutil.which("setxkbmap") is None:
        pytest.skip("Xvfb and setxkbmap are needed")
    server = subprocess.Popen(["Xvfb", ":91", "-nolisten", "tcp", "-noreset"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        monkeypatch.setenv("DISPLAY", ":91")
        real_run = region.run
        monkeypatch.setattr(region, "run", lambda *argv, timeout=15: (
            (_ for _ in ()).throw(region.RegionError("no localed")) if argv[0] == "localectl" else real_run(*argv)))
        region.set_keyboard(("tr", "f"), ("us", ""))
        query = region.parse_setxkbmap_query(real_run("setxkbmap", "-query"))
        assert (query.layouts, query.variants, query.options) == (["tr", "us"], ["f", ""], "grp:alt_shift_toggle")
        region.set_keyboard(("de", ""))
        query = region.parse_setxkbmap_query(real_run("setxkbmap", "-query"))
        assert (query.layouts, query.options) == (["de"], "")  # the Alt+Shift option is gone again
    finally:
        server.terminate()
        server.wait()


@pytest.fixture
def page():
    from kiosk_shell.perdix_hub import RegionPage
    page = RegionPage()
    page.loaded = True
    yield page
    page.shutdown()


def sample_state():
    return (region.RegionStatus(region.TimeStatus("Europe/Istanbul", True, True, True, ""),
                                region.parse_localectl_status(ARCH_LOCALECTL),
                                region.parse_setxkbmap_query("layout: tr\nvariant: f\noptions:\n")),
            ["Europe/Berlin", "Europe/Istanbul", "UTC"], ["C.UTF-8", "en_US.UTF-8", "tr_TR.UTF-8"])


def test_page_shows_state(page):
    page.show_state(sample_state())
    assert page.zone_value.value.text() == "Europe/Istanbul" and page.sync_value.value.text() == "SYNCED"
    assert page.zone.currentText() == "Europe/Istanbul" and page.ntp.isChecked()
    assert page.primary.currentText() == "Türkçe F" and page.secondary.currentText() == "None"  # the session's
    assert page.session_keyboard.text() == "Now: Türkçe F"
    assert page.language.currentData() == "tr_TR.UTF-8" and page.language.currentText() == "Türkçe  (tr_TR.UTF-8)"


def test_page_apply_keyboard(page, monkeypatch):
    calls = []
    monkeypatch.setattr(region, "set_keyboard", lambda p, s: calls.append((p, s)) or "Keyboard: ok.")
    monkeypatch.setattr(page, "load", lambda: None)
    page.show_state(sample_state())
    page.secondary.setCurrentIndex([page.secondary.itemText(i) for i in range(page.secondary.count())]
                                   .index("English (US)"))
    page.apply_keyboard()
    page._worker.wait(5000)
    app.processEvents()
    assert calls == [(("tr", "f"), ("us", ""))] and page.status.text() == "Keyboard: ok."


def test_page_error(page):
    page.show_state(region.RegionError("The system settings service is not running."))
    assert page.status.text() == "The system settings service is not running." and not page.zone_button.isEnabled()


def test_region_is_in_the_system_group():
    from kiosk_shell.perdix_hub import NAV
    assert ("region", "globe", "Region & Language", "Time zone · keyboard · language", "System") in NAV
