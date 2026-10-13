"""Updates: checkupdates parsing and grouping, the install progress, the background check, the
root side (`perdix-tune update` with a stand-in runner), PERDIX notifications (logic, and a real
session bus with notify-send when available) and the page."""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import time

import pytest
from PyQt5.QtWidgets import QApplication, QPushButton

from kiosk_shell.services import updater

app = QApplication.instance() or QApplication([])

CHECKUPDATES = """mesa 1:25.2.4-1 -> 1:25.2.5-1
lib32-mesa 1:25.2.4-1 -> 1:25.2.5-1
linux 6.17.2.arch1-1 -> 6.17.3.arch1-1
steam 1.0.0.83-1 -> 1.0.0.84-1
firefox 143.0-1 -> 143.0.1-1
systemd 258-1 -> 258.1-1
vlc 3.0.21-1 -> 3.0.21-2 [ignored]
"""


def fake_tool(folder, name, script):
    path = folder / name
    path.write_text(f"#!{sys.executable}\n" + script)
    path.chmod(0o755)
    return str(path)


def test_parse_and_group():
    updates = updater.parse_checkupdates(CHECKUPDATES)
    assert len(updates) == 6 and (updates[0].name, updates[0].old, updates[0].new) == (
        "mesa", "1:25.2.4-1", "1:25.2.5-1")
    groups = dict(updater.by_group(updates))
    assert [u.name for u in groups["Graphics driver"]] == ["mesa", "lib32-mesa"]
    assert [u.name for u in groups["Kernel & firmware"]] == ["linux"]
    assert [u.name for u in groups["Games"]] == ["steam"] and [u.name for u in groups["System"]] == ["systemd"]
    assert [u.name for u in groups["Apps"]] == ["firefox"]  # vlc is ignored
    assert [u.name for u in updates if u.needs_restart] == ["mesa", "lib32-mesa", "linux", "systemd"]
    assert updater.summary(updates) == ("6 updates: Graphics driver 2, Kernel & firmware 1, Games 1, System 1, "
                                        "Apps 1 · restart needed afterwards")
    assert updater.highlights(updates) == "mesa 25.2.5-1\nlib32-mesa 25.2.5-1\nlinux 6.17.3.arch1-1"
    assert updater.summary([]) == "PERDIX OS is up to date."


@pytest.mark.parametrize("code,out,err,expected", [
    (2, "", "", []),
    (0, CHECKUPDATES, "", 6),
    (1, "", "==> ERROR: Cannot fetch updates\n", "Could not check for updates: Cannot fetch updates"),
])
def test_check(tmp_path, monkeypatch, code, out, err, expected):
    fake_tool(tmp_path, "checkupdates", f"import sys\nsys.stdout.write({out!r})\nsys.stderr.write({err!r})\n"
                                        f"sys.exit({code})\n")
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    if isinstance(expected, str):
        with pytest.raises(updater.UpdateError, match=expected):
            updater.check()
    elif isinstance(expected, int):
        assert len(updater.check()) == expected
    else:
        assert updater.check() == expected


PACMAN_RUN = """PERDIX: snapshot
PERDIX: keyring
warning: archlinux-keyring-20251001-1 is up to date -- skipping
 there is nothing to do
PERDIX: upgrade
:: Starting full system upgrade...
resolving dependencies...
:: Processing package changes...
(1/3) upgrading lib32-mesa
(2/3) upgrading linux
(3/3) upgrading mesa
:: Running post-transaction hooks...
PERDIX: done restart
"""


def test_progress_follows_the_output():
    progress = updater.Progress()
    seen = []
    for line in PACMAN_RUN.splitlines():
        updater.read_line(progress, line)
        seen.append((progress.percent, progress.text()))
    assert seen[0] == (5, "Taking a safety snapshot…") and seen[1] == (10, "Updating the package keys…")
    assert (43, "Installing lib32-mesa (1/3)…") in seen and (100, "Installing mesa (3/3)…") in seen
    assert progress.phase == "done" and progress.restart and progress.text() == "Done. Restart to finish."


def test_install_streams_pkexec_output(tmp_path, monkeypatch):
    fake_tool(tmp_path, "pkexec", f"import sys\nassert sys.argv[1:] == ['{updater.TUNE}', 'update']\n"
                                  f"sys.stdout.write({PACMAN_RUN!r})\n")
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(updater, "is_live", lambda marker=None: False)
    monkeypatch.setattr(updater, "game_running", lambda: False)
    lines = []
    progress = updater.install(lambda line, p: lines.append((line, p.percent)))
    assert progress.phase == "done" and progress.restart and len(lines) == len(PACMAN_RUN.splitlines())


def test_install_reports_the_failure(tmp_path, monkeypatch):
    fake_tool(tmp_path, "pkexec", "import sys\nprint('PERDIX: upgrade')\n"
                                  "print('perdix-tune: pacman failed (exit code 1); see /var/log/pacman.log')\n"
                                  "sys.exit(1)\n")
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(updater, "is_live", lambda marker=None: False)
    monkeypatch.setattr(updater, "game_running", lambda: False)
    with pytest.raises(updater.UpdateError, match="Pacman failed"):
        updater.install()


def test_install_refused_live_or_in_game(monkeypatch):
    monkeypatch.setattr(updater, "is_live", lambda marker=None: True)
    with pytest.raises(updater.UpdateError, match="live system"):
        updater.install()
    monkeypatch.setattr(updater, "is_live", lambda marker=None: False)
    monkeypatch.setattr(updater, "game_running", lambda: True)
    with pytest.raises(updater.UpdateError, match="A game is running"):
        updater.install()


def test_background_check_announces_once(monkeypatch):
    store = {}
    monkeypatch.setattr(updater.hub_settings, "load", lambda *a: dict(store))
    monkeypatch.setattr(updater.hub_settings, "save_section", lambda name, values: store.update({name: dict(values)}))
    monkeypatch.setattr(updater, "check", lambda: updater.parse_checkupdates(CHECKUPDATES))
    monkeypatch.setattr(updater, "game_running", lambda: False)
    told = []
    monkeypatch.setattr(updater, "notify", told.append)
    updater.check_and_announce()
    updater.check_and_announce()  # the same updates: not announced again
    assert len(told) == 1 and store["updates"]["last_count"] == 6
    monkeypatch.setattr(updater, "game_running", lambda: True)
    monkeypatch.setattr(updater, "check", lambda: pytest.fail("checked during a game"))
    assert updater.check_and_announce() is None


# --- perdix-tune update ---------------------------------------------------------------------------------------


@pytest.fixture
def tune(tmp_path, monkeypatch):
    helper = os.path.join(os.path.dirname(__file__), "..", "..", "rootfs", "usr", "local", "bin", "perdix-tune")
    sys.dont_write_bytecode = True
    loader = importlib.machinery.SourceFileLoader("perdix_tune_update", helper)
    spec = importlib.util.spec_from_loader("perdix_tune_update", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "CONF_DIR", str(tmp_path / "etc"))
    monkeypatch.setattr(module, "LOG_DIR", str(tmp_path / "log"))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)
    # Snapshot set-up on the first update is tested in test_recovery.py; here Timeshift stays unset.
    monkeypatch.setattr(module, "setup_snapshots", lambda path, live_marker=None: None)
    return module


def test_tune_update_runs_snapshot_keyring_upgrade(tune, tmp_path, capsys):
    (tmp_path / "timeshift.json").write_text("{}")
    modules = tmp_path / "modules" / os.uname().release
    modules.mkdir(parents=True)
    calls = []
    restart = tune.update(lambda argv: calls.append(argv) or 0, live_marker=str(tmp_path / "no-live"),
                          lock=str(tmp_path / "no-lock"), timeshift_conf=str(tmp_path / "timeshift.json"),
                          modules_dir=str(tmp_path / "modules"))
    assert restart is False
    assert calls == [["timeshift", "--create", "--scripted", "--quiet", "--comments", "PERDIX: before update"],
                     ["pacman", "-Sy", "--noconfirm", "--needed", "--noprogressbar", "archlinux-keyring"],
                     ["pacman", "-Su", "--noconfirm", "--noprogressbar"]]
    assert capsys.readouterr().out.splitlines() == ["PERDIX: snapshot", "PERDIX: keyring", "PERDIX: upgrade",
                                                    "PERDIX: done"]
    assert "result = ok" in (tmp_path / "etc" / "perdix.conf").read_text()


def test_tune_update_kernel_changed_means_restart(tune, tmp_path, capsys):
    restart = tune.update(lambda argv: 0, live_marker=str(tmp_path / "x"), lock=str(tmp_path / "y"),
                          timeshift_conf=str(tmp_path / "none.json"), modules_dir=str(tmp_path / "modules"))
    assert restart and capsys.readouterr().out.splitlines()[-1] == "PERDIX: done restart"


def test_tune_update_refusals(tune, tmp_path):
    (tmp_path / "live").mkdir()
    with pytest.raises(tune.UsageError, match="live system"):
        tune.update(lambda argv: 0, live_marker=str(tmp_path / "live"))
    (tmp_path / "db.lck").write_text("")
    with pytest.raises(RuntimeError, match="pacman is locked"):
        tune.update(lambda argv: 0, live_marker=str(tmp_path / "x"), lock=str(tmp_path / "db.lck"))
    (tmp_path / "ts.json").write_text("{}")
    calls = []
    with pytest.raises(RuntimeError, match="snapshot failed, so nothing was updated"):
        tune.update(lambda argv: calls.append(argv) or 1, live_marker=str(tmp_path / "x"),
                    lock=str(tmp_path / "y"), timeshift_conf=str(tmp_path / "ts.json"))
    assert len(calls) == 1  # pacman never ran
    with pytest.raises(RuntimeError, match="pacman failed"):
        tune.update(lambda argv: 1, live_marker=str(tmp_path / "x"), lock=str(tmp_path / "y"),
                    timeshift_conf=str(tmp_path / "none.json"))
    assert "result = failed" in (tmp_path / "etc" / "perdix.conf").read_text()


def test_tune_main_accepts_update_only_without_arguments(tune):
    assert tune.main(["update", "--now"]) == 2  # anything else is not a known command


# --- notifications -------------------------------------------------------------------------------------------


def test_notification_server_logic():
    from kiosk_shell import notifications
    server = notifications.NotificationServer()
    events = []
    server.ActionInvoked.connect(lambda n, k: events.append(("action", n, k)))
    server.NotificationClosed.connect(lambda n, r: events.append(("closed", n, r)))
    first = server.notify("PERDIX Updater", 0, "7 updates ready", "mesa <b>25.2.5</b> &amp; more",
                          ["open", "Show updates"], {}, 0)
    second = server.notify("Game recording", 0, "Recording started", "", [], {}, -1)
    assert (first, second) == (1, 2)
    toast = server.toasts[first]
    assert toast.body.text() == "mesa 25.2.5 & more" and list(toast.actions) == ["open"]
    assert server.toasts[second].y() < toast.y()  # newest on top
    assert server.notify("PERDIX Updater", first, "8 updates ready", "", [], {}, 0) == first  # replaced
    assert server.toasts[first].title.text() == "8 updates ready"
    server.invoke(second, "default")  # no default action: just closes
    assert events == [("closed", 2, notifications.DISMISSED)]
    toast = server.toasts[first]
    server.notify("x", 0, "critical", "", ["default", ""], {"urgency": 2}, 1)
    server.invoke(3, "default")
    assert ("action", 3, "default") in events
    server.close(first, notifications.CLOSED_BY_CALL)
    assert not server.toasts


@pytest.mark.skipif(not (shutil.which("dbus-daemon") and shutil.which("notify-send")),
                    reason="needs dbus-daemon and notify-send")
def test_notify_send_on_a_real_session_bus(tmp_path, monkeypatch):
    bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address", f"--address=unix:path={tmp_path}/bus"],
                           stdout=subprocess.PIPE, text=True)
    address = bus.stdout.readline().strip()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    try:
        from PyQt5.QtDBus import QDBusConnection
        from kiosk_shell import notifications
        connection = QDBusConnection.connectToBus(address, "perdix-test")
        server = notifications.NotificationServer()
        assert server.register(connection)
        client = subprocess.Popen(["notify-send", "-a", "PERDIX Updater", "-A", "open=Show updates", "-w",
                                   "3 updates ready", "mesa 25.2.5"], stdout=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        while not server.toasts and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        toast = next(iter(server.toasts.values()))
        assert toast.title.text() == "3 updates ready"
        toast.findChildren(QPushButton)[0].click()
        while client.poll() is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert client.stdout.read().strip() == "open"  # the action reached notify-send
    finally:
        QDBusConnection.disconnectFromBus("perdix-test")
        bus.terminate()
        bus.wait()


# --- the page -------------------------------------------------------------------------------------------------


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(updater, "settings", lambda: {"auto_check": True, "last_check": 0.0, "last_count": 0,
                                                      "announced": ""})
    monkeypatch.setattr(updater, "remember", lambda updates: None)
    monkeypatch.setattr(updater, "is_live", lambda marker=None: False)
    from kiosk_shell.perdix_hub import UpdatesPage
    page = UpdatesPage()
    yield page
    page.shutdown()


def settle(page):
    for _ in range(4):
        for w in (page._worker, page.install_thread):
            if w is not None:
                w.wait(5000)
        app.processEvents()


def test_page_checks_and_lists(page, monkeypatch):
    monkeypatch.setattr(updater, "check", lambda: updater.parse_checkupdates(CHECKUPDATES))
    page.check_button.click()
    settle(page)
    assert page.state_value.value.text() == "6 READY" and page.restart_value.value.text() == "AFTERWARDS"
    groups = [page.tree.topLevelItem(i).text(0) for i in range(page.tree.topLevelItemCount())]
    assert groups == ["Graphics driver  (2)", "Kernel & firmware  (1)", "Games  (1)", "System  (1)", "Apps  (1)"]
    assert page.tree.topLevelItem(1).child(0).text(3) == "restart" and page.install_button.isEnabled()


def test_page_installs_and_offers_restart(page, monkeypatch):
    page.show_updates(updater.parse_checkupdates(CHECKUPDATES), time.time())

    def fake_install(on_line):
        progress = updater.Progress()
        for line in PACMAN_RUN.splitlines():
            on_line(line, updater.read_line(progress, line))
        return progress
    monkeypatch.setattr(updater, "install", fake_install)
    page.install_button.click()
    settle(page)
    assert page.progress_bar.value() == 100 and page.progress_label.text() == "Done. Restart to finish."
    assert "(3/3) upgrading mesa" in page.log.toPlainText()
    assert not page.restart_button.isHidden() and page.state_value.value.text() == "UP TO DATE"


def test_updates_is_in_maintenance():
    from kiosk_shell.perdix_hub import NAV
    assert ("updates", "update", "Updates", "Check · install · restart", "Maintenance") in NAV
