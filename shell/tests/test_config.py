from kiosk_shell.config import load_config


def test_reads_quoted_values_and_ignores_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("KIOSK_STEAM_CMD", raising=False)
    conf = tmp_path / "kiosk.conf"
    conf.write_text('# comment\nKIOSK_STEAM_CMD="steam -bigpicture"  # trailing\nBROKEN="unclosed\n')
    config = load_config(str(conf))
    assert config["KIOSK_STEAM_CMD"] == "steam -bigpicture"
    assert "BROKEN" not in config


def test_missing_file_gives_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("KIOSK_STEAM_CMD", raising=False)
    assert load_config(str(tmp_path / "nope.conf"))["KIOSK_STEAM_CMD"] == "steam -gamepadui"


def test_environment_overrides_file(tmp_path, monkeypatch):
    conf = tmp_path / "kiosk.conf"
    conf.write_text('KIOSK_STEAM_CMD="steam"\n')
    monkeypatch.setenv("KIOSK_STEAM_CMD", "true")
    assert load_config(str(conf))["KIOSK_STEAM_CMD"] == "true"
