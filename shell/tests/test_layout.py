"""The PERDIX file layout: /etc/perdix records, /var/log/perdix logs, ~/.config/perdix-os prefs."""

import configparser
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

import pytest

from kiosk_shell.services import competitor, hud, perflog, theme

HELPER = os.path.join(os.path.dirname(__file__), "..", "..", "rootfs", "usr", "local", "bin", "perdix-tune")
sys.dont_write_bytecode = True
_loader = importlib.machinery.SourceFileLoader("perdix_tune_layout", HELPER)
tune = importlib.util.module_from_spec(importlib.util.spec_from_loader("perdix_tune_layout", _loader))
_loader.exec_module(tune)


@pytest.fixture
def system(tmp_path, monkeypatch):
    """Point the helper's /etc/perdix, /var/log/perdix, /run/perdix and sysfs at tmp_path."""
    for attr, name in (("CONF_DIR", "etc"), ("LOG_DIR", "log"), ("RUN_DIR", "run"), ("SYSFS_DRM", "drm")):
        monkeypatch.setattr(tune, attr, str(tmp_path / name))
    return tmp_path


def ini(path):
    conf = configparser.ConfigParser()
    conf.read(path)
    return conf


def test_records_keep_other_settings(system):
    tune.record("perdix.conf", "network", {"tcp": "bbr"})
    tune.record("perdix.conf", "hud", {"color": "white", "position": "top-left"})
    tune.record("perdix.conf", "network", {"qos": "on"})
    conf = ini(system / "etc" / "perdix.conf")
    assert dict(conf["network"]) == {"tcp": "bbr", "qos": "on"}
    assert conf["hud"]["color"] == "white"
    assert (system / "etc" / "perdix.conf").read_text().startswith("# PERDIX OS perdix.conf")
    assert tune.main(["hud", "white", "bottom-right"]) == 0
    assert tune.main(["hud", "purple", "middle"]) == 2
    assert tune.main(["game-mode", "on"]) == 0
    assert ini(system / "etc" / "perdix.conf")["game_mode"]["competitor"] == "on"
    assert "competitor mode on" in (system / "log" / "game_mode.log").read_text()


def test_services_pause_and_resume_are_logged(system, monkeypatch):
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        active = cmd[:3] == ["systemctl", "is-active", "--quiet"] and cmd[3] in ("man-db.timer", "clamav-freshclam.service")
        return subprocess.CompletedProcess(cmd, 0 if active or cmd[1] in ("stop", "start") else 3)

    monkeypatch.setattr(tune.subprocess, "run", fake_run)
    assert tune.pause_services() == ["clamav-freshclam.service", "man-db.timer"]
    assert ["systemctl", "stop", "sshd.service"] not in calls          # only whitelisted units
    assert tune.resume_services() == ["clamav-freshclam.service", "man-db.timer"]
    log = (system / "log" / "game_mode.log").read_text().splitlines()
    assert log[0].endswith("game mode: stopped clamav-freshclam.service, man-db.timer")
    assert log[1].endswith("game mode: restarted clamav-freshclam.service, man-db.timer")


def test_gpu_profile_writes_amdgpu_sysfs_and_drivers_conf(system):
    level = system / "drm" / "card1" / "device" / "power_dpm_force_performance_level"
    level.parent.mkdir(parents=True)
    level.write_text("auto")
    assert tune.main(["gpu", "performance"]) == 0
    assert level.read_text() == "high"
    assert dict(ini(system / "etc" / "drivers.conf")["gpu"]) == {"profile": "performance", "cards": "card1"}
    assert tune.main(["gpu", "turbo"]) == 2
    level.unlink()
    assert tune.main(["gpu", "auto"]) == 1                            # no adjustable GPU


def test_restore_reapplies_and_rejects_tampered_values(system, monkeypatch):
    applied = []
    monkeypatch.setattr(tune, "run_plan", lambda commands, cleanup: applied.append(commands) or "")
    monkeypatch.setattr(tune, "default_interface", lambda wait=0: "eth0")
    monkeypatch.setattr(tune, "check_iface", lambda name: name)
    tune.record("perdix.conf", "network", {"tcp": "bbr", "qos": "on", "download_limit_mbit": "50",
                                           "game_ports": "27015-27050"})
    assert tune.restore() == []
    assert applied[0] == [["sysctl", "-w", "net.ipv4.tcp_congestion_control=bbr"]]
    assert applied[1][0][:6] == ["tc", "qdisc", "add", "dev", "eth0", "root"]
    assert ini(system / "etc" / "perdix.conf")["game_mode"]["competitor"] == "off"

    tune.record("perdix.conf", "network", {"tcp": "bbr; reboot", "game_ports": "1-2;rm"})
    applied.clear()
    problems = tune.restore()
    assert applied == [] and problems and "QoS not restored" in problems[0]


MANGOHUD_CSV = """os,cpu,gpu,ram,kernel,driver,cpuscheduler
Arch Linux,AMD Ryzen 7 5800X,AMD Radeon RX 6700 XT,32 GB,6.11.1-zen1,Mesa 24.2,
--------------------FRAME METRICS--------------------
fps,frametime,cpu_load,gpu_load,cpu_temp,gpu_temp,elapsed
250,4.0,30,95,60,70,0
250,4.0,30,95,60,70,1000000000
125,8.0,30,95,60,70,2000000000
500,2.0,30,95,60,70,61000000000
"""


def test_perf_log_summarises_mangohud_sessions(tmp_path):
    logs, perf = tmp_path / "mangohud", tmp_path / "perf.log"
    logs.mkdir()
    (logs / "cs2_2026-10-12_21-04-11.csv").write_text(MANGOHUD_CSV)
    (logs / "cs2_2026-10-12_21-04-11_summary.csv").write_text("ignored")
    (logs / "broken_2026-10-12_21-00-00.csv").write_text("no frame data here\n")
    assert perflog.collect(str(logs), str(perf)) == 1
    line = perf.read_text().strip()
    assert "game=cs2 duration=1m01s avg_fps=281.2 low1_fps=125.0" in line
    assert "frametime_avg_ms=4.50 frametime_p99_ms=8.00" in line
    assert sorted(os.listdir(logs)) == ["broken_2026-10-12_21-00-00.csv.done", "cs2_2026-10-12_21-04-11.csv.done",
                                        "cs2_2026-10-12_21-04-11_summary.csv"]
    assert perflog.collect(str(logs), str(perf)) == 0                   # nothing new
    assert perflog.game_name("Counter-Strike 2_2026-10-12_21-04-11.csv") == "Counter-Strike 2"


def test_theme_colours_validated_and_created(tmp_path):
    path = tmp_path / "perdix-os" / "theme.json"
    assert theme.load(str(path)) == theme.DEFAULTS
    assert json.loads(path.read_text()) == theme.DEFAULTS               # created for the user to edit
    path.write_text(json.dumps({"orange": "#FF0000", "ice": "not a colour", "blue": 7}))
    assert theme.load(str(path)) == {**theme.DEFAULTS, "orange": "#ff0000"}


def test_hud_perf_logging_option_can_be_removed(tmp_path):
    path = tmp_path / "MangoHud.conf"
    hud.apply_settings(hud.HudSettings(record_perf=True), str(path))
    assert "output_folder=/var/log/perdix/mangohud" in path.read_text()
    hud.apply_settings(hud.HudSettings(record_perf=False), str(path))
    text = path.read_text()
    assert "output_folder" not in text and "autostart_log" not in text and "fps=1" in text


def test_competitor_mode_ends_with_the_boot(tmp_path, monkeypatch):
    state, env = tmp_path / "competitor.json", tmp_path / "competitor.env"
    monkeypatch.setattr(competitor, "ENV_PATH", str(env))
    monkeypatch.setattr(competitor.tuning, "read_first_line", lambda _path: "boot-2")
    competitor.write_env(str(env))
    state.write_text(json.dumps({"gamemoded_pid": None, "boot_id": "boot-1"}))
    assert not competitor.is_active(str(state))                         # set before a reboot
    state.write_text(json.dumps({"gamemoded_pid": None, "boot_id": "boot-2"}))
    assert competitor.is_active(str(state))
