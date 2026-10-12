import pytest

from kiosk_shell.services import hub_settings, hud, macros, mouse

XINPUT_LIST = """\
⎡ Virtual core pointer                    	id=2	[master pointer  (3)]
⎜   ↳ Virtual core XTEST pointer              	id=4	[slave  pointer  (2)]
⎜   ↳ Logitech G Pro X Superlight             	id=10	[slave  pointer  (2)]
⎜   ↳ SynPS/2 Synaptics TouchPad              	id=13	[slave  pointer  (2)]
⎣ Virtual core keyboard                   	id=3	[master keyboard (2)]
    ↳ Logitech G Pro X Superlight Keyboard    	id=11	[slave  keyboard (3)]
"""


def test_pointer_list_skips_xtest_and_keyboards():
    assert [(p.id, p.name) for p in mouse.parse_pointer_list(XINPUT_LIST)] == [
        (10, "Logitech G Pro X Superlight"),
        (13, "SynPS/2 Synaptics TouchPad"),
    ]


def test_props_and_profile_values():
    props = mouse.parse_props(
        "Device 'Mouse':\n\tlibinput Accel Speed (318):\t-0.250000\n"
        "\tlibinput Accel Profile Enabled (320):\t1, 0, 0\n"
    )
    assert props[mouse.SPEED_PROP] == "-0.250000"
    assert mouse.profile_values(props[mouse.PROFILE_PROP], mouse.FLAT) == ["0", "1", "0"]
    assert mouse.profile_values(props[mouse.PROFILE_PROP], mouse.CUSTOM) == ["0", "0", "1"]
    assert mouse.profile_values("0, 1", mouse.ADAPTIVE) == ["1", "0"]
    with pytest.raises(ValueError):  # older libinput: no custom profile
        mouse.profile_values("0, 1", mouse.CUSTOM)


def test_acceleration_curves():
    for curve in ("linear", "natural", "classic"):
        assert mouse.multiplier(curve, 0.5, 0) == 1.0          # no boost when the hand is still
        assert mouse.multiplier(curve, 0.0, 40) == 1.0         # amount 0 = no acceleration
        assert mouse.multiplier(curve, 0.5, 30) > mouse.multiplier(curve, 0.5, 10) > 1.0
    assert mouse.multiplier("linear", 1.0, 1000) == mouse.CAP
    assert mouse.multiplier("natural", 1.0, 1000) == pytest.approx(4.0)
    assert mouse.multiplier("off", 1.0, 30) == 1.0
    points = mouse.custom_points("linear", 0.5)
    assert len(points) == 51 <= 64                             # libinput's limit
    assert points[0] == 0 and points[10] == pytest.approx(10 * 1.4)
    assert points == sorted(points)                            # output speed never decreases


def test_sensitivity_maths():
    # 800 DPI at CS2 sens 1.0: 360 / 0.022 counts = 16363.6 counts -> 51.95 cm
    assert mouse.cm_per_360(800, 1.0, mouse.GAMES["Counter-Strike 2"]) == pytest.approx(51.95, abs=0.01)
    assert mouse.edpi(800, 1.25) == 1000
    assert mouse.convert_sens(1.0, 0.022, 0.07) == pytest.approx(0.3143, abs=1e-4)  # CS2 -> Valorant


def test_combos_and_text():
    assert macros.parse_combo("Ctrl + Shift + R") == ["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_R"]
    assert macros.parse_combo("f12") == ["KEY_F12"]
    with pytest.raises(ValueError):
        macros.parse_combo("ctrl+hyper")
    assert macros.text_to_keys("gg!") == [("KEY_G", False), ("KEY_G", False), ("KEY_1", True)]
    with pytest.raises(ValueError):
        macros.text_to_keys("ç")


def test_validate_and_round_trip(tmp_path):
    good = macros.Macro("Buy AK", [macros.Step("key", "b"), macros.Step("delay", "50"), macros.Step("text", "41")])
    bad = macros.Macro("", [macros.Step("delay", "0"), macros.Step("key", "nope")])
    assert macros.validate(good) == []
    assert macros.validate(bad) == [
        "The macro needs a name.",
        "Step 1: delay must be 1-60000 ms.",
        "Step 2: unknown key 'nope'.",
    ]
    path = tmp_path / "macros.json"
    macros.save_macros([good], str(path))
    assert macros.load_macros(str(path)) == [good]
    path.write_text("{broken")
    assert macros.load_macros(str(path)) == []


def test_theme_engine_edits_config_in_place(tmp_path):
    path = tmp_path / "MangoHud" / "MangoHud.conf"
    path.parent.mkdir()
    path.write_text("# my own notes\nposition=top-right\nfps_limit=240\n")
    hud.apply_theme("white", "bottom-right", str(path))
    text = path.read_text()
    assert "# my own notes" in text and "fps_limit=240" in text   # user lines kept
    assert "position=bottom-right" in text and "position=top-right" not in text
    assert "text_color=FFFFFF" in text
    hud.apply_theme("orange", "top-left", str(path))
    text = path.read_text()
    assert text.count("position=") == 1 and "cpu_color=FF6A1A" in text
    with pytest.raises(ValueError):
        hud.apply_theme("green", "top-left", str(path))


def test_hud_settings_writes_every_metric_explicitly(tmp_path):
    path = tmp_path / "MangoHud.conf"
    hud.apply_settings(hud.HudSettings(metrics={"fps": True}), str(path))
    text = path.read_text()
    assert "fps=1" in text and "cpu_stats=0" in text and "frame_timing=0" in text
    assert hud.HudSettings(color="purple", position="middle").color == "orange"


def test_settings_merge_ignores_wrong_types(tmp_path):
    defaults = {"speed": 0.0, "flat": True, "device": "", "metrics": {"fps": True, "ram": False}}
    saved = {"speed": 0.5, "flat": "yes", "device": 3, "metrics": {"ram": True, "bogus": 1}}
    assert hub_settings.merged(defaults, saved) == {
        "speed": 0.5, "flat": True, "device": "", "metrics": {"fps": True, "ram": True},
    }
    path = tmp_path / "hub.json"
    hub_settings.save_section("mouse", {"speed": 0.2}, str(path))
    hub_settings.save_section("hud", {"position": "top-left"}, str(path))
    assert hub_settings.load(str(path)) == {"mouse": {"speed": 0.2}, "hud": {"position": "top-left"}}
