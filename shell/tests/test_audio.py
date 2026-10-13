"""Audio: PipeWire parsing (with a real, trimmed pw-dump), EasyEffects presets and control, the 08 page."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import audio

app = QApplication.instance() or QApplication([])
PW_DUMP = json.loads((Path(__file__).parent / "data" / "pw-dump.json").read_text())


# --- PipeWire -------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("Volume: 0.45\n", audio.Volume(0.45, False)),
    ("Volume: 1.00 [MUTED]\n", audio.Volume(1.0, True)),
])
def test_parse_volume(text, expected):
    assert audio.parse_volume(text) == expected


def test_parse_volume_without_device():
    with pytest.raises(audio.AudioError, match="No sound device"):
        audio.parse_volume("")


def test_devices_from_real_pw_dump():
    # Recorded from PipeWire 1.0.5 + WirePlumber with two virtual sinks and a virtual microphone,
    # after the headset was made the default.
    sinks = audio.parse_devices(PW_DUMP, "Audio/Sink")
    assert [(d.description, d.is_default) for d in sinks] == [("Test Headset", True), ("Test Speakers", False)]
    sources = audio.parse_devices(PW_DUMP, "Audio/Source")
    assert [(d.name, d.is_default) for d in sources] == [("perdix-test-mic", True)]


def test_easyeffects_devices_are_hidden():
    dump = PW_DUMP + [{"id": 99, "type": "PipeWire:Interface:Node",
                       "info": {"props": {"media.class": "Audio/Sink", "node.name": "easyeffects_sink",
                                          "node.description": "Easy Effects Sink"}}}]
    assert "Easy Effects Sink" not in [d.description for d in audio.parse_devices(dump, "Audio/Sink")]


@pytest.fixture
def commands(monkeypatch):
    """Records commands instead of running them; answers wpctl get-volume."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        out = "Volume: 0.30\n" if argv[:2] == ["wpctl", "get-volume"] else ""
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(audio.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(audio.subprocess, "run", run)
    return calls


def test_volume_commands(commands):
    audio.set_volume(1.7)  # louder than 100 % is the Volume Boost effect's job
    audio.set_volume(0.333, audio.SOURCE)
    audio.set_muted(True)
    audio.set_default(audio.Device(43, "x", "Headset"))
    assert commands == [
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.00"],
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SOURCE@", "0.33"],
        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"],
        ["wpctl", "set-default", "43"],
    ]


def test_pipewire_not_running(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(audio.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(
        argv, 1, "", "Could not connect to PipeWire"))
    with pytest.raises(audio.AudioError, match="sound service"):
        audio.get_volume()


# --- EasyEffects presets ----------------------------------------------------------------------------


def test_effects_from_saved_settings():
    assert audio.Effects.from_dict({"volume_boost": "high", "bass_boost": "loud", "noise_suppression": "yes"}) \
        == audio.Effects("high", "off", False)
    assert audio.Effects.from_dict(None) == audio.Effects()
    assert not audio.Effects().any and audio.Effects(noise_suppression=True).any


def test_output_preset():
    preset = audio.output_preset(audio.Effects("medium", "high"))["output"]
    assert preset["plugins_order"] == ["bass_enhancer#0", "limiter#0"]
    assert preset["bass_enhancer#0"]["amount"] == 10.0
    limiter = preset["limiter#0"]
    assert limiter["input-gain"] == 6.0 and limiter["threshold"] == -1.0 and limiter["gain-boost"] is False
    # Bass alone still gets the limiter (at 0 dB extra) so the added bass can't clip.
    alone = audio.output_preset(audio.Effects(bass_boost="low"))["output"]
    assert alone["plugins_order"] == ["bass_enhancer#0", "limiter#0"] and alone["limiter#0"]["input-gain"] == 0.0
    assert audio.output_preset(audio.Effects())["output"]["plugins_order"] == []


def test_input_preset():
    assert audio.input_preset(audio.Effects(noise_suppression=True))["input"]["plugins_order"] == ["rnnoise#0"]
    assert audio.input_preset(audio.Effects())["input"] == {"blocklist": [], "plugins_order": []}


def test_write_presets(tmp_path):
    audio.write_presets(audio.Effects("low", "off", True), tmp_path)
    output = json.loads((tmp_path / "output" / "perdix-output.json").read_text())
    mic = json.loads((tmp_path / "input" / "perdix-mic.json").read_text())
    assert output["output"]["limiter#0"]["input-gain"] == 3.0 and mic["input"]["plugins_order"] == ["rnnoise#0"]
    assert sorted(p.name for p in tmp_path.rglob("*")) == ["input", "output", "perdix-mic.json", "perdix-output.json"]


def test_presets_dir_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert audio.presets_dir() == tmp_path / "easyeffects"  # EasyEffects 8 reads ~/.local/share/easyeffects


# --- EasyEffects control ----------------------------------------------------------------------------


@pytest.fixture
def ee(monkeypatch, tmp_path):
    """EasyEffects stand-in: tracks whether it runs and which commands it got."""
    state = {"running": False, "calls": [], "started": 0}
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(audio, "easyeffects_installed", lambda: True)
    monkeypatch.setattr(audio, "easyeffects_running", lambda: state["running"])

    def start():
        state["started"] += 1
        state["running"] = True

    def run(*argv, timeout=5):
        state["calls"].append(list(argv))
        if argv[1] == "-q":
            state["running"] = False
        return ""
    monkeypatch.setattr(audio, "start_service", start)
    monkeypatch.setattr(audio, "run", run)
    return state


def test_effects_on_starts_easyeffects_and_loads_both_presets(ee, tmp_path):
    message = audio.apply_effects(audio.Effects("high", "off", True))
    assert message == ("Effects on: volume boost +9 dB, microphone noise suppression. "
                       "Turn them all off for the lowest latency.")
    assert ee["started"] == 1
    assert ee["calls"] == [["easyeffects", "-l", "perdix-output"], ["easyeffects", "-l", "perdix-mic"]]
    assert (tmp_path / "data" / "easyeffects" / "output" / "perdix-output.json").exists()
    audio.apply_effects(audio.Effects(bass_boost="low"))
    assert ee["started"] == 1  # already running: just load the new presets


def test_all_effects_off_quits_easyeffects(ee):
    ee["running"] = True
    assert "no added latency" in audio.apply_effects(audio.Effects())
    assert ee["calls"] == [["easyeffects", "-q"]] and not ee["running"]
    audio.apply_effects(audio.Effects())
    assert ee["calls"] == [["easyeffects", "-q"]]  # nothing to stop the second time


def test_missing_preset_is_reported(ee, monkeypatch):
    monkeypatch.setattr(audio, "run", lambda *argv, timeout=5: "Specified preset does not exist.\n")
    ee["running"] = True
    with pytest.raises(audio.AudioError, match="perdix-output"):
        audio.apply_effects(audio.Effects("low"))


def test_start_service_waits_for_the_socket(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    started = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            started.append((argv, kwargs["start_new_session"]))
            (tmp_path / "EasyEffectsServer").touch()  # what EasyEffects 8 creates once it listens
    monkeypatch.setattr(audio.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(audio, "easyeffects_running", lambda: True)
    audio.start_service()
    assert started == [(["easyeffects", "--service-mode"], True)]


def test_start_service_gives_up(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(audio.subprocess, "Popen", lambda argv, **kwargs: None)
    monkeypatch.setattr(audio, "SERVICE_START_WAIT", 0.3)
    with pytest.raises(audio.AudioError, match="did not start"):
        audio.start_service()


def test_not_installed(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    with pytest.raises(audio.AudioError, match="EasyEffects is not installed"):
        audio.apply_effects(audio.Effects("low"))


# --- the 08 page and login restore -----------------------------------------------------------------


@pytest.fixture
def page(monkeypatch):
    saved = {}
    from kiosk_shell import perdix_hub
    monkeypatch.setattr(perdix_hub.hub_settings, "load", lambda *a: {"audio": saved.get("audio")})
    monkeypatch.setattr(perdix_hub.hub_settings, "save_section", lambda name, values, *a: saved.update({name: values}))
    monkeypatch.setattr(audio, "devices", lambda kind: audio.parse_devices(PW_DUMP, kind))
    monkeypatch.setattr(audio, "get_volume", lambda target=audio.SINK: audio.Volume(0.42, target == audio.SOURCE))
    monkeypatch.setattr(audio, "easyeffects_installed", lambda: True)
    monkeypatch.setattr(audio, "easyeffects_running", lambda: False)
    page = perdix_hub.AudioPage()
    page.saved = saved
    return page


def settle(page):
    for _ in range(5):
        if page._worker is not None:
            page._worker.wait(5000)
        app.processEvents()


def test_page_shows_pipewire_state(page):
    page.refresh()
    assert [page.output_device.itemText(i) for i in range(page.output_device.count())] == ["Test Headset",
                                                                                            "Test Speakers"]
    assert page.output_volume.value() == 42 and not page.output_mute.isChecked()
    assert page.input_mute.isChecked()  # the stand-in reports the microphone as muted


def test_volume_slider_sets_pipewire(page, monkeypatch):
    calls = []
    monkeypatch.setattr(audio, "set_volume", lambda level, target: calls.append((round(level, 2), target)))
    page.refresh()
    page.output_volume.slider.setValue(80)
    page._send_volumes()
    assert (0.8, audio.SINK) in calls


def test_one_click_effect(page, monkeypatch):
    applied = []
    monkeypatch.setattr(audio, "apply_effects", lambda e: applied.append(e) or "Effects on: bass boost (medium).")
    medium = next(b for b in page.bass_group.buttons() if b.property("value") == "medium")
    medium.click()
    settle(page)
    assert applied == [audio.Effects("off", "medium", False)]
    assert page.saved["audio"] == {"volume_boost": "off", "bass_boost": "medium", "noise_suppression": False}
    assert page.effects_status.text() == "Effects on: bass boost (medium)."


def test_effect_error_is_shown(page, monkeypatch):
    def fail(effects):
        raise audio.AudioError("EasyEffects did not start.")
    monkeypatch.setattr(audio, "apply_effects", fail)
    next(b for b in page.noise_group.buttons() if b.property("value") == "on").click()
    settle(page)
    assert page.effects_status.text() == "EasyEffects did not start."
    assert page.effects_status.objectName() == "StatusError"


def test_apply_saved_audio_effects(monkeypatch):
    from kiosk_shell import perdix_hub
    applied = []
    monkeypatch.setattr(audio, "easyeffects_installed", lambda: True)
    monkeypatch.setattr(audio, "apply_effects", lambda e: applied.append(e) or "ok")
    monkeypatch.setattr(perdix_hub.hub_settings, "load", lambda *a: {})
    perdix_hub.apply_saved_audio_effects()
    assert applied == []  # all off at login: EasyEffects is not even started
    monkeypatch.setattr(perdix_hub.hub_settings, "load", lambda *a: {"audio": {"noise_suppression": True}})
    perdix_hub.apply_saved_audio_effects()
    assert applied == [audio.Effects(noise_suppression=True)]


def test_audio_is_in_the_menu():
    from kiosk_shell.perdix_hub import NAV
    assert ("audio", "audio", "Audio", "Volume · EasyEffects", "Network & Sound") in NAV


def test_runtime_dir_default(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert audio.server_socket() == Path(f"/run/user/{os.getuid()}/EasyEffectsServer")
