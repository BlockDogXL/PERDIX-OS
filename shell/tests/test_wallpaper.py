"""Desktop wallpapers: the PERDIX defaults, the user's own folder, the settings, and the desktop
showing them in turn."""

import os
import time

import pytest
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import wallpaper

app = QApplication.instance() or QApplication([])
REPO_WALLPAPERS = os.path.join(os.path.dirname(__file__), "..", "..", "rootfs", "usr", "share", "backgrounds",
                               "perdix")


def test_defaults_ship_in_rootfs():
    names = sorted(os.listdir(REPO_WALLPAPERS))
    assert names == ["01-keklik-sabah.jpg", "02-keklik-dere.jpg", "03-keklik-zirve.jpg"]
    for name in names:
        assert not QImage(os.path.join(REPO_WALLPAPERS, name)).isNull()


def picture(path, color):
    image = QImage(32, 18, QImage.Format_RGB32)
    image.fill(QColor(color))
    image.save(str(path))
    return str(path)


def test_available_adds_the_users_pictures(tmp_path):
    system, user = tmp_path / "system", tmp_path / "user"
    system.mkdir()
    user.mkdir()
    picture(system / "01.jpg", "red")
    picture(user / "mine.png", "blue")
    (user / "notes.txt").write_text("x")
    assert [os.path.basename(p) for p in wallpaper.available(str(system), str(user))] == ["01.jpg", "mine.png"]
    # no system folder (running from the repository): the copy in rootfs/ is used
    found = wallpaper.available(str(tmp_path / "missing"), str(tmp_path / "none"))
    assert [os.path.basename(p) for p in found] == ["01-keklik-sabah.jpg", "02-keklik-dere.jpg",
                                                    "03-keklik-zirve.jpg"]


def test_playlist_modes(tmp_path):
    images = ["/a.jpg", "/b.jpg"]
    fixed = picture(tmp_path / "f.png", "green")
    assert wallpaper.playlist({"mode": "rotate", "file": "", "minutes": 30}, images) == images
    assert wallpaper.playlist({"mode": "off", "file": "", "minutes": 30}, images) == []
    assert wallpaper.playlist({"mode": "fixed", "file": fixed, "minutes": 30}, images) == [fixed]
    assert wallpaper.playlist({"mode": "fixed", "file": "/gone.jpg", "minutes": 30}, images) == ["/a.jpg"]


def test_settings_are_checked(monkeypatch):
    monkeypatch.setattr(wallpaper.hub_settings, "load", lambda *a: {"wallpaper": {"mode": "spin", "minutes": 0}})
    assert wallpaper.settings() == {"mode": "rotate", "file": "", "minutes": 1}


@pytest.fixture
def backdrop(tmp_path, monkeypatch):
    from kiosk_shell.main_desktop import Backdrop
    paths = [picture(tmp_path / "1.png", "#ff0000"), str(tmp_path / "broken.png"), picture(tmp_path / "2.png", "#0000ff")]
    (tmp_path / "broken.png").write_text("not a picture")
    monkeypatch.setattr(wallpaper, "playlist", lambda values=None: paths)
    widget = Backdrop()
    widget.resize(320, 180)
    yield widget
    widget.close()


def centre(widget):
    return widget.grab().toImage().pixelColor(160, 90)


def test_backdrop_shows_and_rotates(backdrop):
    backdrop.load_wallpapers({"mode": "rotate", "file": "", "minutes": 30})
    assert backdrop.wallpaper_path.endswith("1.png") and backdrop._rotate.interval() == 30 * 60_000
    first = centre(backdrop)
    assert first.red() > first.blue()  # red picture under the dark veil
    backdrop.next_wallpaper()  # the unreadable picture is skipped
    assert backdrop.wallpaper_path.endswith("2.png")
    deadline = time.monotonic() + backdrop.FADE_MS / 1000 + 1
    while backdrop._previous is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    second = centre(backdrop)
    assert second.blue() > second.red()


def test_backdrop_without_wallpapers(backdrop, monkeypatch):
    monkeypatch.setattr(wallpaper, "playlist", lambda values=None: [])
    backdrop.load_wallpapers({"mode": "off", "file": "", "minutes": 30})
    assert backdrop._current is None and not backdrop._rotate.isActive() and backdrop.wallpaper_path == ""
