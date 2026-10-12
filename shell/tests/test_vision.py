"""PERDIX Vision: colour pipeline, .cube files, gamescope control and the 09 page.

The gamescope side was also checked outside these tests: the generated .cube loads in gamescope's own
LoadCubeLut with identical values, and PerdixCAS.fxh compiles with gamescope's ReShade compiler."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import vision

app = QApplication.instance() or QApplication([])
S = vision.VisionSettings


# --- colour pipeline ------------------------------------------------------------------------------


def test_neutral_is_identity():
    table = vision.build_table(S(), size=9)
    grid = np.linspace(0, 1, 9)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    assert np.allclose(table, np.stack([r, g, b], -1))
    assert S().colour_neutral and S(sharpening=80).colour_neutral and not S(lift=1).colour_neutral


def grey(value, settings):
    return vision.adjust(np.array([value, value, value]), settings)[0]


def test_shadow_boost_lifts_shadows_more_than_highlights():
    boost = S(lift=20, gamma=25)
    assert grey(0.05, boost) - 0.05 > 0.15  # an enemy in a dark corner becomes visible
    assert grey(0.95, boost) - 0.95 < 0.05  # bright areas barely change
    assert grey(1.0, S(gain=-50)) == pytest.approx(0.5)
    assert grey(0.5, S(gamma=-50)) == pytest.approx(0.25)  # gamma 0.5 darkens mid-tones


def test_brightness_and_contrast():
    assert grey(0.5, S(brightness=20)) == pytest.approx(0.6)
    assert grey(0.75, S(contrast=50)) == pytest.approx(0.875)
    assert grey(0.5, S(contrast=50)) == pytest.approx(0.5)  # mid-grey is the pivot


def test_vibrance_protects_saturated_colours():
    dull, vivid = np.array([0.5, 0.4, 0.4]), np.array([0.9, 0.1, 0.1])
    spread = lambda c: c.max() - c.min()  # noqa: E731
    gain_dull = spread(vision.adjust(dull, S(vibrance=60))) / spread(dull)
    gain_vivid = spread(vision.adjust(vivid, S(vibrance=60))) / spread(vivid)
    assert gain_dull > gain_vivid > 1.0
    assert np.ptp(vision.adjust(vivid, S(vibrance=-100))) == pytest.approx(0.0)  # grey


def test_settings_are_clamped_and_validated():
    s = S.from_dict({"brightness": 400, "vibrance": -3.6, "enabled": "yes", "lut": "../../etc/x.cube",
                     "sharpening": True})
    assert (s.brightness, s.vibrance, s.enabled, s.lut, s.sharpening) == (50, -4, False, "", 0)
    assert S.from_dict(S(gamma=10, lut="Film.cube").to_dict()) == S(gamma=10, lut="Film.cube")


# --- .cube files ----------------------------------------------------------------------------------------


def cube(size=2, body=None, header=""):
    if body is None:
        grid = np.linspace(0, 1, size)
        body = "\n".join(f"{r} {g} {b}" for b in grid for g in grid for r in grid)
    return f"# made by a tool\nTITLE \"Test\"\n{header}LUT_3D_SIZE {size}\n{body}\n"


def test_parse_cube_identity_and_sample():
    lut = vision.parse_cube(cube(5))
    points = np.random.default_rng(1).random((50, 3))
    assert np.allclose(vision.sample(lut, points), points)
    assert lut.title == "Test"


def test_parse_cube_respects_domain():
    # A LUT whose input domain is 0-2: input 1.0 is the middle of the table.
    lut = vision.parse_cube(cube(2, header="DOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n"))
    assert np.allclose(vision.sample(lut, np.array([1.0, 1.0, 1.0])), [0.5, 0.5, 0.5])
    resolve = vision.parse_cube(cube(2, header="LUT_3D_INPUT_RANGE 0 2\n"))
    assert np.allclose(resolve.domain_max, [2, 2, 2])


def test_red_changes_fastest():
    # Swap red and blue: only right if the first index of each line is red.
    grid = np.linspace(0, 1, 3)
    body = "\n".join(f"{b} {g} {r}" for b in grid for g in grid for r in grid)
    lut = vision.parse_cube(cube(3, body))
    assert np.allclose(vision.sample(lut, np.array([1.0, 0.0, 0.0])), [0, 0, 1])


@pytest.mark.parametrize("text, message", [
    ("LUT_1D_SIZE 4\n0 0 0\n", "1D LUT"),
    ("TITLE \"x\"\n0 0 0\n", "no LUT_3D_SIZE"),
    (cube(2, "0 0 0\n1 1 1"), "8 entries but has 2"),
    (cube(2, "0 0 zero\n" * 8), "expected 3 numbers"),
    (cube(2, "0 0 0 0\n" * 8), "expected 3 numbers"),
    ("LUT_3D_SIZE 129\n", "not supported"),
    (cube(2, header="DOMAIN_MIN 1 1 1\nDOMAIN_MAX 0 0 0\n"), "DOMAIN_MAX"),
])
def test_parse_cube_rejects(text, message):
    with pytest.raises(vision.VisionError, match=message):
        vision.parse_cube(text)


def test_cube_text_matches_gamescope_reader_rules():
    text = vision.cube_text(vision.build_table(S(lift=10), size=3))
    lines = text.splitlines()
    assert "LUT_3D_SIZE 3" in lines
    data = [line for line in lines if line[:1].isdigit()]
    assert len(data) == 27 and all(len(line.split()) == 3 for line in data)
    assert "," not in text  # gamescope reads with sscanf("%f"): '.' decimal point only
    assert vision.parse_cube(text).size == 3  # and it reads back


def test_lut_library(tmp_path):
    source = tmp_path / "My Film Look!.cube"
    source.write_text(cube(3))
    library = tmp_path / "luts"
    assert vision.import_lut(str(source), library) == "My Film Look_.cube"
    assert vision.import_lut(str(source), library) == "My Film Look_ (2).cube"
    assert vision.list_luts(library) == ["My Film Look_ (2).cube", "My Film Look_.cube"]
    assert vision.load_lut("My Film Look_.cube", library).size == 3
    vision.delete_lut("My Film Look_ (2).cube", library)
    assert vision.list_luts(library) == ["My Film Look_.cube"]
    bad = tmp_path / "broken.cube"
    bad.write_text("not a lut")
    with pytest.raises(vision.VisionError, match="no LUT_3D_SIZE"):
        vision.import_lut(str(bad), library)
    with pytest.raises(vision.VisionError, match="Invalid"):
        vision.delete_lut("../settings.json", library)


# --- files for gamescope ----------------------------------------------------------------------------------


def test_write_look_combines_lut_and_sliders(tmp_path):
    library = tmp_path / "luts"
    library.mkdir()
    grid = np.linspace(0, 1, 3)  # a LUT that inverts the image
    (library / "Invert.cube").write_text(cube(3, "\n".join(f"{1 - r} {1 - g} {1 - b}" for b in grid for g in grid
                                                           for r in grid)))
    out = tmp_path / "look.cube"
    assert vision.write_look(S(), out, library) is None  # nothing to do
    vision.write_look(S(lut="Invert.cube", lut_strength=100, brightness=20), out, library)
    look = vision.parse_cube(out.read_text())
    assert look.size == vision.LUT_SIZE
    assert np.allclose(vision.sample(look, np.array([0.0, 0.0, 0.0])), [1.0, 1.0, 1.0])
    assert np.allclose(vision.sample(look, np.array([1.0, 1.0, 1.0])), [0.1, 0.1, 0.1])  # inverted, then +0.1
    half = vision.write_look(S(lut="Invert.cube", lut_strength=50), out, library)
    assert np.allclose(vision.sample(vision.parse_cube(half.read_text()), np.array([1.0, 0, 0])), [0.5, 0.5, 0.5])


def test_write_shader(tmp_path):
    assert vision.write_shader(65, tmp_path) == "perdix_cas_065.fx"
    assert '#define PERDIX_SHARPNESS 0.65' in (tmp_path / "perdix_cas_065.fx").read_text()
    assert "technique PerdixCAS" in (tmp_path / "PerdixCAS.fxh").read_text()
    assert vision.write_shader(30, tmp_path) == "perdix_cas_030.fx"
    assert sorted(p.name for p in tmp_path.glob("perdix_cas_*")) == ["perdix_cas_030.fx"]  # old one gone
    assert vision.write_shader(0, tmp_path) is None and not list(tmp_path.glob("perdix_cas_*"))


# --- gamescope control ---------------------------------------------------------------------------------


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A running PERDIX gamescope session (this process stands in for it) and recorded xprop calls."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "perdix").mkdir()
    (tmp_path / "perdix" / "gamescope-display").write_text(f":1 {os.getpid()}\n")
    monkeypatch.setattr(vision, "LOOK_FILE", tmp_path / "state" / "perdix-vision.cube")
    monkeypatch.setattr(vision, "SHADER_DIR", tmp_path / "shaders")
    monkeypatch.setattr(vision, "LUT_DIR", tmp_path / "luts")
    calls = []
    monkeypatch.setattr(vision, "set_string", lambda d, atom, value: calls.append((d, "string", atom, value)))
    monkeypatch.setattr(vision, "set_cardinal", lambda d, atom, value: calls.append((d, "cardinal", atom, value)))
    monkeypatch.setattr(vision, "remove", lambda d, atom: calls.append((d, "remove", atom)))
    return calls


def test_apply_live(session, tmp_path):
    message = vision.apply(S(enabled=True, gamma=20, sharpening=40))
    assert message == "Applied live to the running games."
    look = str(tmp_path / "state" / "perdix-vision.cube")
    assert session == [
        (":1", "string", "GAMESCOPE_COLOR_LOOK_G22", look),
        (":1", "cardinal", "GAMESCOPE_RESHADE_TECHNIQUE_IDX", 0),
        (":1", "string", "GAMESCOPE_RESHADE_EFFECT", "perdix_cas_040.fx"),
    ]
    session.clear()
    assert vision.apply(S(enabled=False, gamma=20)) == "Vision effects removed from the running games."
    assert session == [(":1", "remove", "GAMESCOPE_COLOR_LOOK_G22"), (":1", "remove", "GAMESCOPE_RESHADE_EFFECT")]


def test_apply_without_session(session, tmp_path, monkeypatch):
    (tmp_path / "perdix" / "gamescope-display").write_text(":1 999999999\n")  # gamescope has exited
    monkeypatch.setattr(vision, "gamescope_installed", lambda: True)
    assert vision.apply(S(enabled=True, lift=5)) == "Saved. Applied to games as soon as you open Steam."
    assert session == [] and (tmp_path / "state" / "perdix-vision.cube").exists()
    monkeypatch.setattr(vision, "gamescope_installed", lambda: False)
    with pytest.raises(vision.VisionError, match="gamescope is not installed"):
        vision.apply(S(enabled=True))


def test_gamescope_session_script(tmp_path):
    """The inner shell of gamescope_argv: records the display, applies the settings, becomes Steam."""
    steam_log = tmp_path / "steam.log"
    fake_python = tmp_path / "python"
    fake_python.write_text(f'#!/bin/sh\necho "apply: PYTHONPATH=$PYTHONPATH $*" >>"{steam_log}"\n')
    fake_python.chmod(0o755)
    argv = vision.gamescope_argv(["sh", "-c", f'echo "steam $$ on $DISPLAY" >>"{steam_log}"'], 2560, 1440,
                                 str(fake_python), "/opt/kiosk-shell")
    assert argv[:9] == ["gamescope", "-e", "-f", "-W", "2560", "-H", "1440", "--", "sh"]
    env = {**os.environ, "DISPLAY": ":7", "XDG_RUNTIME_DIR": str(tmp_path)}
    process = subprocess.run(argv[8:], env=env, capture_output=True, text=True, timeout=10)
    assert process.returncode == 0, process.stderr
    display, pid = (tmp_path / "perdix" / "gamescope-display").read_text().split()
    lines = steam_log.read_text().splitlines()
    assert display == ":7"
    assert lines[0] == "apply: PYTHONPATH=/opt/kiosk-shell -m kiosk_shell.services.vision --apply-saved"
    assert lines[1] == f"steam {pid} on :7"  # Steam replaced the shell: the recorded pid is Steam's


def test_properties_on_a_real_x_server(tmp_path):
    """Set and remove the properties on a real X server's root window (the atoms do not exist yet,
    which xprop -set would silently skip), and read them back with xprop."""
    if not (os.path.exists("/usr/bin/Xvfb") and os.path.exists("/usr/bin/xprop")):
        pytest.skip("Xvfb and xprop are needed")
    # -noreset: Xvfb otherwise wipes the properties when its last client disconnects (gamescope's own
    # X server always has gamescope and Steam connected).
    server = subprocess.Popen(["Xvfb", ":93", "-nolisten", "tcp", "-noreset"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if subprocess.run(["xprop", "-display", ":93", "-root", "-len", "1"], capture_output=True).returncode == 0:
                break
            subprocess.run(["sleep", "0.1"])
        vision.set_string(":93", vision.ATOM_LOOK, "/home/kiosk/.local/state/perdix/vision/perdix-vision.cube")
        vision.set_cardinal(":93", vision.ATOM_RESHADE_TECHNIQUE, 0)
        read = lambda atom: subprocess.run(["xprop", "-display", ":93", "-root", atom],  # noqa: E731
                                           capture_output=True, text=True).stdout.strip()
        assert read(vision.ATOM_LOOK) == ('GAMESCOPE_COLOR_LOOK_G22(UTF8_STRING) = '
                                          '"/home/kiosk/.local/state/perdix/vision/perdix-vision.cube"')
        assert read(vision.ATOM_RESHADE_TECHNIQUE) == "GAMESCOPE_RESHADE_TECHNIQUE_IDX(CARDINAL) = 0"
        vision.remove(":93", vision.ATOM_LOOK)
        assert "not found" in read(vision.ATOM_LOOK)
        with pytest.raises(vision.VisionError, match="Could not reach gamescope"):
            vision.set_cardinal(":94", vision.ATOM_RESHADE_TECHNIQUE, 0)  # no X server there
    finally:
        server.terminate()
        server.wait()


def test_apply_saved_command(monkeypatch, capsys, tmp_path):
    from kiosk_shell.services import hub_settings
    monkeypatch.setattr(vision, "SHADER_DIR", tmp_path / "shaders")
    monkeypatch.setattr(hub_settings, "load", lambda *a: {"vision": {"enabled": False}})
    assert vision.main(["--apply-saved"]) == 0
    assert "off" in capsys.readouterr().out


# --- desktop and the 09 page -----------------------------------------------------------------------------


def test_desktop_wraps_steam_in_gamescope(monkeypatch, tmp_path):
    from kiosk_shell import main_desktop
    from kiosk_shell.services import hub_settings
    monkeypatch.setenv("HOME", str(tmp_path))
    window = main_desktop.MainDesktop({"KIOSK_STEAM_CMD": "steam -gamepadui"})
    monkeypatch.setattr(vision, "gamescope_installed", lambda: True)
    monkeypatch.setattr(hub_settings, "load", lambda *a: {"vision": {"enabled": True}})
    argv = window.steam_argv()
    assert argv[:2] == ["gamescope", "-e"] and argv[-1].endswith("exec steam -gamepadui")
    assert f"{shlex.quote(sys.executable)} -m kiosk_shell.services.vision --apply-saved" in argv[-1]
    monkeypatch.setattr(hub_settings, "load", lambda *a: {"vision": {"enabled": False}})
    assert window.steam_argv() is None
    window._allow_close = True
    window.close()


@pytest.fixture
def page(monkeypatch, tmp_path):
    from kiosk_shell import perdix_hub
    saved = {}
    monkeypatch.setattr(perdix_hub.hub_settings, "load", lambda *a: {"vision": saved.get("vision")})
    monkeypatch.setattr(perdix_hub.hub_settings, "save_section", lambda name, values, *a: saved.update({name: values}))
    monkeypatch.setattr(vision, "LUT_DIR", tmp_path / "luts")
    monkeypatch.setattr(vision, "gamescope_installed", lambda: True)
    monkeypatch.setattr(vision, "session_display", lambda: None)
    page = perdix_hub.VisionPage()
    page.saved = saved
    return page


def settle(page):
    for _ in range(5):
        if page._worker is not None:
            page._worker.wait(5000)
        app.processEvents()


def test_page_slider_applies_and_saves(page, monkeypatch):
    applied = []
    monkeypatch.setattr(vision, "apply", lambda s: applied.append(s) or "Applied live to the running games.")
    page.enabled.setChecked(True)
    page.sliders["lift"].slider.setValue(15)
    page.sliders["sharpening"].slider.setValue(40)
    page._apply_timer.stop()
    page.apply()
    settle(page)
    assert applied[-1] == S(enabled=True, lift=15, sharpening=40)
    assert page.saved["vision"]["lift"] == 15
    assert page.status.text() == "Applied live to the running games."


def test_page_preview_shows_the_change(page):
    before = page.preview._image.copy()
    page.enabled.setChecked(True)
    page.sliders["lift"].slider.setValue(30)
    after = page.preview._image
    left, right = (before.pixelColor(20, 60), after.pixelColor(20, 60)), (before.pixelColor(240, 60),
                                                                          after.pixelColor(240, 60))
    assert left[0] == left[1]  # BEFORE half unchanged
    assert right[1].lightness() > right[0].lightness()  # AFTER half: shadows lifted


def test_page_lut_import_and_remove(page, monkeypatch, tmp_path):
    source = tmp_path / "Night Ops.cube"
    source.write_text(cube(3))
    monkeypatch.setattr(page, "choose_file", lambda: str(source))
    page.import_clicked()
    assert [page.lut_list.item(i).text() for i in range(page.lut_list.count())] == ["None", "Night Ops"]
    assert page.current().lut == "Night Ops.cube" and page.remove_button.isEnabled()
    page.remove_clicked()
    assert page.lut_list.count() == 1 and page.current().lut == ""


def test_page_rejects_bad_lut(page, monkeypatch, tmp_path):
    bad = tmp_path / "bad.cube"
    bad.write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")
    monkeypatch.setattr(page, "choose_file", lambda: str(bad))
    page.import_clicked()
    assert "1D LUT" in page.status.text() and page.status.objectName() == "StatusError"


def test_page_reset(page):
    page.sliders["gain"].slider.setValue(20)
    page.reset()
    assert page.current() == S(enabled=page.enabled.isChecked())


def test_vision_is_in_the_menu():
    from kiosk_shell.perdix_hub import NAV
    assert ("vision", "vision", "Vision", "Colour · sharpening · LUT", "Gaming") in NAV


def test_shader_resource_is_packaged():
    assert Path(vision.SHADER_TEMPLATE).read_text().count("tex2D(PerdixBackBuffer") == 9
