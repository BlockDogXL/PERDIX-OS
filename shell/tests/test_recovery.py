"""Recovery center: `perdix-tune snapshot ...` against output recorded from real Timeshift 24.01
(tests/data/timeshift-list*.txt, made with a loop-device backup disk), its checks, the client and the
page. Real-run findings these tests keep: no "--tags O" (Timeshift rejects it), "--quiet" on create,
and restore answers Timeshift's ENTER prompts on stdin."""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QApplication, QMessageBox

from kiosk_shell.services import recovery

app = QApplication.instance() or QApplication([])
DATA = Path(__file__).parent / "data"
LIST = (DATA / "timeshift-list.txt").read_text()


@pytest.fixture
def tune(tmp_path, monkeypatch):
    helper = Path(__file__).parent.parent.parent / "rootfs" / "usr" / "local" / "bin" / "perdix-tune"
    sys.dont_write_bytecode = True
    loader = importlib.machinery.SourceFileLoader("perdix_tune_recovery", str(helper))
    spec = importlib.util.spec_from_loader("perdix_tune_recovery", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "LOG_DIR", str(tmp_path / "log"))
    return module


class Runner:
    def __init__(self, listing=LIST, code=0, output=""):
        self.calls, self.listing, self.code, self.output = [], listing, code, output

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs.get("input")))
        if argv[:2] == ["timeshift", "--list"]:
            return SimpleNamespace(returncode=0, stdout=self.listing, stderr="")
        return SimpleNamespace(returncode=self.code, stdout=self.output, stderr="")


def test_parse_real_list(tune):
    info = tune.parse_timeshift_list(LIST)
    assert (info["device"], info["mode"], info["status"], info["free"]) == ("/dev/loop0", "RSYNC", "OK", "1.8 GB")
    assert info["snapshots"] == [
        {"name": "2026-10-13_13-28-01", "tags": "O", "description": "PERDIX: before update"},
        {"name": "2026-10-13_13-28-46", "tags": "D", "description": "Daily"}]
    first_run = tune.parse_timeshift_list((DATA / "timeshift-list-unconfigured.txt").read_text())
    assert first_run["status"] == "OK" and len(first_run["snapshots"]) == 2


def conf(tmp_path):
    path = tmp_path / "timeshift.json"
    path.write_text("{}")
    return str(path)


def test_create_list_delete_restore_commands(tune, tmp_path, capsys):
    runner, c, live = Runner(), conf(tmp_path), str(tmp_path / "no-live")
    assert tune.snapshot(["list"], runner, c, live) == 0
    assert json.loads(capsys.readouterr().out)["configured"] is True
    tune.snapshot(["create", "Before installing mods"], runner, c, live)
    assert runner.calls[-1] == (["timeshift", "--create", "--scripted", "--quiet", "--comments",
                                 "Before installing mods"], None)
    tune.snapshot(["delete", "2026-10-13_13-28-46"], runner, c, live)
    assert runner.calls[-1] == (["timeshift", "--delete", "--snapshot", "2026-10-13_13-28-46", "--scripted",
                                 "--quiet"], None)
    tune.snapshot(["restore", "2026-10-13_13-28-01"], runner, c, live)
    argv, stdin = runner.calls[-1]
    assert argv == ["timeshift", "--restore", "--snapshot", "2026-10-13_13-28-01", "--scripted", "--yes",
                    "--skip-grub", "--quiet"]
    assert stdin == "\n" * 20  # Timeshift's "Press ENTER" prompts, even with --yes
    assert not any("--tags" in a for a, _ in runner.calls)


def test_snapshot_refusals(tune, tmp_path):
    runner, c, live = Runner(), conf(tmp_path), str(tmp_path / "no-live")
    for argv, message in ((["delete", "../../etc"], "not a snapshot name"),
                          (["restore", "2026-01-01_00-00-00"], "no snapshot called"),
                          (["create", "two\nlines"], "1-60 printable"), (["create", "x" * 61], "1-60 printable"),
                          (["wipe"], "unknown snapshot command")):
        with pytest.raises(tune.UsageError, match=message):
            tune.snapshot(argv, runner, c, live)
    (tmp_path / "live").mkdir()
    with pytest.raises(tune.UsageError, match="live system"):
        tune.snapshot(["create", "x"], runner, c, str(tmp_path / "live"))
    with pytest.raises(tune.UsageError, match="not set up yet"):
        tune.snapshot(["create", "x"], runner, str(tmp_path / "none.json"), live)
    assert all(argv[:2] == ["timeshift", "--list"] for argv, _ in runner.calls)  # nothing else ran


def test_timeshift_error_is_reported(tune, tmp_path):
    runner = Runner(code=1, output="E: Not enough disk space (< 1000 MB)\n")
    with pytest.raises(RuntimeError, match="Not enough disk space"):
        tune.snapshot(["create", "x"], runner, conf(tmp_path), str(tmp_path / "x"))


def test_list_when_not_set_up(tune, tmp_path, capsys):
    tune.snapshot(["list"], Runner(), str(tmp_path / "none.json"), str(tmp_path / "x"))
    assert json.loads(capsys.readouterr().out) == {"configured": False, "device": "", "mode": "", "status": "",
                                                   "free": "", "snapshots": []}


def test_setup_writes_timeshift_config(tune, tmp_path, monkeypatch):
    defaults = tmp_path / "default.json"
    defaults.write_text(json.dumps({"backup_device_uuid": "", "do_first_run": "true", "count_daily": "5",
                                    "snapshot_size": "0", "exclude": [], "exclude-apps": []}))
    monkeypatch.setattr(tune, "root_filesystem", lambda: ("1a70-uuid", False))
    path = tmp_path / "etc" / "timeshift.json"
    tune.setup_snapshots(str(path), str(defaults), str(tmp_path / "x"))
    config = json.loads(path.read_text())
    assert config["backup_device_uuid"] == "1a70-uuid" and config["do_first_run"] == "false"
    assert config["btrfs_mode"] == "false" and config["schedule_daily"] == "true" and config["count_weekly"] == "3"
    assert "/home/**" in config["exclude"] and config["snapshot_size"] == "0"  # Timeshift's own keys are kept
    assert all(isinstance(v, (str, list)) for v in config.values())  # Timeshift stores strings


def test_btrfs_only_with_the_at_layout(tune, monkeypatch):
    def findmnt(uuid, fstype, options):
        return json.dumps({"filesystems": [{"uuid": uuid, "fstype": fstype, "options": options}]})
    outputs = {"btrfs-at": findmnt("u1", "btrfs", "rw,relatime,subvol=/@"),
               "btrfs-other": findmnt("u2", "btrfs", "rw,subvol=/root"), "ext4": findmnt("u3", "ext4", "rw,relatime"),
               "no-uuid": findmnt(None, "ext4", "rw,relatime")}
    for key, expected in (("btrfs-at", ("u1", True)), ("btrfs-other", ("u2", False)), ("ext4", ("u3", False))):
        monkeypatch.setattr(tune.subprocess, "run", lambda *a, k=key, **kw: SimpleNamespace(stdout=outputs[k]))
        assert tune.root_filesystem() == expected
    # a disk without a UUID (as in this test container) is refused, never written as "ext4"
    monkeypatch.setattr(tune.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=outputs["no-uuid"]))
    with pytest.raises(RuntimeError, match="UUID"):
        tune.root_filesystem()


def test_first_update_sets_snapshots_up(tune, tmp_path, monkeypatch):
    monkeypatch.setattr(tune.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(tune, "CONF_DIR", str(tmp_path / "etc"))
    made = []
    monkeypatch.setattr(tune, "setup_snapshots", lambda path, live_marker: made.append(path) or open(path, "w").close())
    calls = []
    tune.update(lambda argv: calls.append(argv) or 0, live_marker=str(tmp_path / "x"), lock=str(tmp_path / "y"),
                timeshift_conf=str(tmp_path / "ts.json"), modules_dir=str(tmp_path))
    assert made == [str(tmp_path / "ts.json")] and calls[0][:2] == ["timeshift", "--create"]


def test_snapshot_timer_is_paused_in_game_mode(tune):
    assert "perdix-snapshot.timer" in tune.PAUSABLE_UNITS
    units = Path(__file__).parent.parent.parent / "rootfs" / "etc" / "systemd" / "system"
    service = (units / "perdix-snapshot.service").read_text()
    assert "ExecStart=/usr/bin/timeshift --check --scripted --quiet" in service
    assert "ConditionPathExists=/etc/timeshift/timeshift.json" in service
    assert os.readlink(units / "timers.target.wants" / "perdix-snapshot.timer") == \
        "/etc/systemd/system/perdix-snapshot.timer"


# --- client ---------------------------------------------------------------------------------------------------


def test_client_state_and_kinds(tmp_path, monkeypatch):
    payload = {"configured": True, "device": "/dev/nvme0n1p2", "mode": "RSYNC", "status": "OK", "free": "212 GB",
               "snapshots": [{"name": "2026-10-13_13-28-01", "tags": "O", "description": "PERDIX: before update"},
                             {"name": "2026-10-14_09-00-00", "tags": "D", "description": ""},
                             {"name": "2026-10-15_09-00-00", "tags": "DW", "description": ""}]}
    script = tmp_path / "pkexec"
    script.write_text(f"#!{sys.executable}\nimport sys, json\nassert sys.argv[2:] == ['snapshot', 'list']\n"
                      f"print(json.dumps({payload!r}))\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(recovery.shutil, "which", lambda name: str(tmp_path / name) if name == "pkexec" else "/x")
    state = recovery.state()
    assert state.configured and state.free == "212 GB" and state.newest.name == "2026-10-15_09-00-00"
    assert [s.kind for s in state.snapshots] == ["Before update", "Daily", "Daily, Weekly"]
    assert state.snapshots[0].taken.strftime("%d.%m.%Y %H:%M") == "13.10.2026 13:28"


def test_client_error_message(tmp_path, monkeypatch):
    script = tmp_path / "pkexec"
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('perdix-tune: snapshots are not set up yet\\n')\n"
                      "sys.exit(2)\n")
    script.chmod(0o755)
    monkeypatch.setattr(recovery.shutil, "which", lambda name: str(script))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    with pytest.raises(recovery.RecoveryError, match="^Snapshots are not set up yet.$"):
        recovery.create("x")


# --- the page --------------------------------------------------------------------------------------------------


STATE = recovery.State(True, "/dev/nvme0n1p2", "RSYNC", "OK", "212 GB", [
    recovery.Snapshot("2026-10-13_13-28-01", "O", "PERDIX: before update"),
    recovery.Snapshot("2026-10-14_09-00-00", "D", ""),
    recovery.Snapshot("2026-10-14_20-15-33", "O", "Before installing mods")])


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(recovery, "is_live", lambda marker=None: False)
    monkeypatch.setattr(recovery, "state", lambda: STATE)
    from kiosk_shell.perdix_hub import RecoveryPage
    page = RecoveryPage()
    yield page
    page.shutdown()


def settle(page):
    for _ in range(4):
        if page._worker is not None:
            page._worker.wait(5000)
        app.processEvents()


def test_page_lists_newest_first(page):
    page.refresh()
    settle(page)
    rows = [[page.table.item(r, c).text() for c in range(3)] for r in range(page.table.rowCount())]
    assert rows == [["14.10.2026  20:15", "Manual", "Before installing mods"], ["14.10.2026  09:00", "Daily", ""],
                    ["13.10.2026  13:28", "Before update", "PERDIX: before update"]]
    assert page.count_value.value.text() == "3" and page.newest_value.value.text() == "14.10. 20:15"
    assert page.setup_button.isHidden() and page.create_button.isEnabled() and not page.restore_button.isEnabled()


def test_page_restore_asks_first(page, monkeypatch):
    page.refresh()
    settle(page)
    page.table.selectRow(2)
    restored = []
    monkeypatch.setattr(recovery, "restore", lambda snap: restored.append(snap.name) or "Restored. The PC restarts now.")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Cancel)
    page.restore_button.click()
    settle(page)
    assert restored == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    page.table.selectRow(2)
    page.restore_button.click()
    settle(page)
    assert restored == ["2026-10-13_13-28-01"]


def test_page_take_snapshot(page, monkeypatch):
    page.refresh()
    settle(page)
    taken = []
    monkeypatch.setattr(recovery, "create", lambda comment: taken.append(comment) or "Snapshot taken.")
    page.comment.setText("  Before driver test ")
    page.create_button.click()
    settle(page)
    assert taken == ["Before driver test"] and page.status.text() == "Snapshot taken."


def test_page_not_set_up(page, monkeypatch):
    monkeypatch.setattr(recovery, "state", lambda: recovery.State(False))
    page.refresh()
    settle(page)
    assert not page.setup_button.isHidden() and not page.create_button.isEnabled()
    assert "not set up yet" in page.status.text()


def test_recovery_is_in_maintenance():
    from kiosk_shell.perdix_hub import NAV
    assert ("recovery", "restore", "Recovery", "Snapshots · restore", "Maintenance") in NAV
