"""Sharing: the stream-mix logic against a pw-dump recorded from real PipeWire 1.0.5 (a speaker, a
Bluetooth headset as the default output, a mono USB mic, the stream mix and Discord recording from
it), link planning, the checks, and the page."""

import json
from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import sharing

app = QApplication.instance() or QApplication([])
DUMP = json.loads((Path(__file__).parent / "data" / "pw-dump-sharing.json").read_text())


def test_parse_graph():
    g = sharing.parse_graph(DUMP)
    assert (g.default_sink, g.default_source) == ("bt-headset", "usb-mic")
    assert g.mix.media_class == "Audio/Source/Virtual" and g.mix.description == "PERDIX Stream Mix"
    assert sorted(p.name for p in g.mix.inputs()) == ["input_FL", "input_FR"]
    assert [p.channel for p in g.nodes["usb-mic"].outputs()] == ["MONO"]
    assert [(s.app, s.media_class) for s in g.streams] == [("Discord", "Stream/Input/Audio")]


def test_status_and_recording_apps():
    g = sharing.parse_graph(DUMP)
    status = sharing.mix_status(g)
    assert status.text() == "PERDIX Stream Mix carries game sound from BT Headset + microphone USB Mic."
    assert sharing.recording_apps(g) == [("Discord", "PERDIX Stream Mix")]


def test_wanted_links_follow_the_defaults():
    g = sharing.parse_graph(DUMP)
    links = {(l.out_node, l.out_port, l.in_port) for l in sharing.wanted_links(g, True, True)}
    assert links == {("bt-headset", "monitor_FL", "input_FL"), ("bt-headset", "monitor_FR", "input_FR"),
                     ("usb-mic", "capture_MONO", "input_FL"), ("usb-mic", "capture_MONO", "input_FR")}
    assert {l.out_node for l in sharing.wanted_links(g, False, True)} == {"usb-mic"}
    assert {l.out_node for l in sharing.wanted_links(g, True, False)} == {"bt-headset"}
    g.default_sink = "game-speakers"
    assert {l.out_node for l in sharing.wanted_links(g, True, False)} == {"game-speakers"}


def test_never_feeds_the_mix_into_itself():
    g = sharing.parse_graph(DUMP)
    g.default_source = sharing.MIX_NAME
    assert {l.out_node for l in sharing.wanted_links(g, True, True)} == {"bt-headset"}
    g.default_source = "game-speakers"  # no microphone: the default "source" is a speaker's monitor
    assert {l.out_node for l in sharing.wanted_links(g, True, True)} == {"bt-headset"}


def test_channel_sides():
    assert [sharing.side(c) for c in ("FL", "FR", "MONO", "FC", "LFE", "RL", "SR")] == [
        ("FL",), ("FR",), ("FL", "FR"), ("FL", "FR"), (), ("FL",), ("FR",)]


def test_sync_only_changes_what_differs(monkeypatch):
    g = sharing.parse_graph(DUMP)
    g.default_sink = "game-speakers"  # the output changed: the headset links must go
    calls = []
    monkeypatch.setattr(sharing, "graph", lambda: g)
    monkeypatch.setattr(sharing, "run", lambda *argv, **kw: calls.append(argv) or "")
    sharing.sync(True, True)
    assert sorted(calls) == [
        ("pw-link", "-d", "bt-headset:monitor_FL", "perdix-stream-mix:input_FL"),
        ("pw-link", "-d", "bt-headset:monitor_FR", "perdix-stream-mix:input_FR"),
        ("pw-link", "game-speakers:monitor_FL", "perdix-stream-mix:input_FL"),
        ("pw-link", "game-speakers:monitor_FR", "perdix-stream-mix:input_FR"),
    ]


def test_sync_creates_the_mix_when_missing(monkeypatch):
    without = [o for o in DUMP if (o.get("info") or {}).get("props", {}).get("node.name") != sharing.MIX_NAME]
    graphs = [sharing.parse_graph(without), sharing.parse_graph(DUMP), sharing.parse_graph(DUMP),
              sharing.parse_graph(DUMP)]
    calls = []
    monkeypatch.setattr(sharing, "graph", lambda: graphs.pop(0) if len(graphs) > 1 else graphs[0])
    monkeypatch.setattr(sharing, "run", lambda *argv, **kw: calls.append(argv) or "")
    sharing.sync()
    create = calls[0]
    assert create[:3] == ("pw-cli", "create-node", "adapter")
    for prop in ("node.name=perdix-stream-mix", "media.class=Audio/Source/Virtual", "object.linger=true",
                 "priority.session=1"):
        assert prop in create[3]


def test_apply_saves_and_removes(monkeypatch, tmp_path):
    saved = {}
    monkeypatch.setattr(sharing.hub_settings, "save_section", lambda name, values: saved.update({name: values}))
    calls = []
    monkeypatch.setattr(sharing, "graph", lambda: sharing.parse_graph(DUMP))
    monkeypatch.setattr(sharing, "run", lambda *argv, **kw: calls.append(argv) or "")
    assert sharing.apply({"enabled": False, "game": True, "mic": True}).exists is False
    mix_id = sharing.parse_graph(DUMP).mix.id
    assert calls == [("pw-cli", "destroy", str(mix_id))] and saved["sharing"]["enabled"] is False


def test_run_reports_missing_pipewire(monkeypatch):
    monkeypatch.setattr(sharing.shutil, "which", lambda name: None)
    with pytest.raises(sharing.SharingError, match="not installed"):
        sharing.graph()


def test_checks(monkeypatch, tmp_path):
    portals = tmp_path / "portals"
    portals.mkdir()
    (portals / "gtk.portal").write_text("[portal]\n")
    (tmp_path / "pulse").mkdir()
    (tmp_path / "pulse" / "native").write_text("")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(sharing, "_process_running", lambda name: name == "wireplumber")
    monkeypatch.setattr(sharing.shutil, "which", lambda name: "/usr/bin/" + name)
    found = {c.name: c for c in sharing.checks(sharing.parse_graph(DUMP), "", str(portals), str(tmp_path))}
    assert all(c.ok for c in found.values()), found
    assert "Xorg" in found["Screen capture"].detail and "gtk" in found["Desktop portal"].detail
    (tmp_path / "pulse" / "native").unlink()
    found = {c.name: c for c in sharing.checks(None, "The sound service (PipeWire) is not running.", str(portals),
                                               str(tmp_path))}
    assert found["PipeWire"].ok is False and found["Discord & browsers sound"].ok is False


# --- the page ------------------------------------------------------------------------------------------------


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(sharing, "settings", lambda: {"enabled": True, "game": True, "mic": True})
    monkeypatch.setattr(sharing, "graph", lambda: sharing.parse_graph(DUMP))
    monkeypatch.setattr(sharing, "checks", lambda g, error="": [sharing.Check("PipeWire", True, "running")])
    from kiosk_shell.perdix_hub import SharingPage
    page = SharingPage()
    yield page
    page.shutdown()


def settle(page):
    for _ in range(3):
        if page._worker is not None:
            page._worker.wait(5000)
        app.processEvents()


def test_page_shows_the_mix_and_who_records(page):
    page.refresh()
    settle(page)
    assert page.mix_status.text().startswith("PERDIX Stream Mix carries game sound from BT Headset")
    assert page.recording.text() == "Discord  ←  PERDIX Stream Mix"
    assert page.checks_grid.count() == 3


def test_page_switching_off_applies(page, monkeypatch):
    applied = []
    monkeypatch.setattr(sharing, "apply", lambda values: applied.append(values) or sharing.MixStatus(False))
    page.enabled.click()
    settle(page)
    assert applied == [{"enabled": False, "game": True, "mic": True}]
    assert not page.game.isEnabled() and not page.mic.isEnabled()


def test_sharing_is_in_the_media_group():
    from kiosk_shell.perdix_hub import NAV
    assert ("sharing", "share", "Sharing", "Screen · stream mix", "Media") in NAV
