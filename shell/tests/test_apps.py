"""Office and Drawing: file associations, the app runner (with stand-in programs) and the desktop."""

import os
import stat
import time

import pytest
from PyQt5.QtCore import QDir
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import apps

app = QApplication.instance() or QApplication([])


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """Stand-in `libreoffice` and `krita` on PATH: they log their arguments and stay open briefly."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("libreoffice", "krita"):
        script = bin_dir / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >>"{log}"\nsleep "${{FAKE_SLEEP:-0.5}}"\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    return condition()


@pytest.mark.parametrize("name, expected", [
    ("Bütçe 2025.XLSX", apps.OFFICE), ("sunum.pptx", apps.OFFICE), ("rapor.docx", apps.OFFICE),
    ("tablo.ods", apps.OFFICE), ("notlar.txt", apps.OFFICE), ("kitap.pdf", apps.OFFICE),
    ("piri.kra", apps.DRAWING), ("ekran.PNG", apps.DRAWING), ("eskiz.psd", apps.DRAWING),
    ("oyun.exe", None), ("README", None),
])
def test_app_for(name, expected):
    assert apps.app_for(name) is expected


def test_argv_uses_absolute_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # A file named like an option can't be mistaken for one.
    assert apps.OFFICE.argv("-safe-mode.xlsx") == ["--nologo", str(tmp_path / "-safe-mode.xlsx")]
    assert apps.DRAWING.argv() == ["--nosplash"]


def test_runner_starts_then_hands_files_to_the_running_app(fake_bin, tmp_path):
    runner = apps.AppRunner(apps.OFFICE)
    events = []
    runner.started.connect(lambda: events.append("started"))
    runner.finished.connect(lambda code: events.append(("finished", code)))
    doc = tmp_path / "bütçe.xlsx"
    doc.write_text("x")

    assert runner.open(str(doc)) == "Opening bütçe.xlsx in LibreOffice…"
    assert wait_for(lambda: "started" in events) and runner.is_running()
    assert runner.open(str(tmp_path / "sunum.pptx")) == "Opening sunum.pptx in LibreOffice…"
    assert wait_for(lambda: len(fake_bin.read_text().splitlines()) == 2)
    assert wait_for(lambda: ("finished", 0) in events)
    assert fake_bin.read_text().splitlines() == [
        f"libreoffice --nologo {doc}",
        f"libreoffice --nologo {tmp_path / 'sunum.pptx'}",
    ]


def test_tile_while_running_raises_the_window(fake_bin, monkeypatch):
    raised = []
    monkeypatch.setattr(apps, "raise_window", lambda a: raised.append(a.name) or True)
    runner = apps.AppRunner(apps.DRAWING)
    assert runner.open() == "Starting Krita…"
    assert wait_for(runner.is_running)
    assert runner.open() == "Krita is already open."
    assert raised == ["Krita"]
    assert wait_for(lambda: not runner.is_running())


def test_missing_app(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    runner = apps.AppRunner(apps.OFFICE)
    errors = []
    runner.failed.connect(errors.append)
    assert runner.open("x.xlsx") == ""
    assert errors == ["LibreOffice is not installed."]


# --- desktop --------------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Belgeler").mkdir(parents=True)
    (home / "Belgeler" / "bütçe.xlsx").write_text("x")
    (home / "oyun.exe").write_text("x")
    (tmp_path / "secret.docx").write_text("x")
    (home / "escape.docx").symlink_to(tmp_path / "secret.docx")
    monkeypatch.setenv("HOME", str(home))
    assert QDir.homePath() == str(home)
    return home


def index_of(page, path):
    page.model.setRootPath(str(path.parent))
    deadline = time.monotonic() + 5
    while page.model.rowCount(page.model.index(str(path.parent))) == 0 and time.monotonic() < deadline:
        app.processEvents()
    return page.model.index(str(path))


def test_files_page_opens_documents(home):
    from kiosk_shell.main_desktop import FilesPage
    opened = []
    page = FilesPage(lambda: None, lambda path: opened.append(path) or "Opening…")
    page._open(index_of(page, home / "Belgeler"))
    assert page.current == str(home / "Belgeler")
    page._open(index_of(page, home / "Belgeler" / "bütçe.xlsx"))
    assert opened == [str(home / "Belgeler" / "bütçe.xlsx")] and page.info.text() == "Opening…"
    page._describe(index_of(page, home / "Belgeler" / "bütçe.xlsx"))
    assert page.info.text().endswith("opens in LibreOffice")
    page._open(index_of(page, home / "escape.docx"))  # symlink out of the home folder
    assert len(opened) == 1 and page.info.text() == "That file is outside your files."


@pytest.fixture
def desktop(home):
    from kiosk_shell.main_desktop import MainDesktop
    window = MainDesktop({"KIOSK_STEAM_CMD": "true"})
    yield window
    window._allow_close = True
    window.close()


def test_desktop_routes_files_to_their_app(desktop, home, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop.office, "open", lambda path=None: calls.append(("office", path)) or "ok")
    monkeypatch.setattr(desktop.drawing, "open", lambda path=None: calls.append(("drawing", path)) or "ok")
    assert desktop.open_file(str(home / "Belgeler" / "bütçe.xlsx")) == "ok"
    assert desktop.open_file(str(home / "piri.kra")) == "ok"
    assert desktop.open_file(str(home / "oyun.exe")) == "No app opens .exe files."
    assert calls == [("office", str(home / "Belgeler" / "bütçe.xlsx")), ("drawing", str(home / "piri.kra"))]
    desktop.home.office_button.click()
    desktop.home.drawing_button.click()
    assert calls[-2:] == [("office", None), ("drawing", None)]


def test_desktop_badges_follow_the_app(desktop, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "0.3")
    desktop.home.drawing_button.click()
    assert wait_for(lambda: desktop.home.drawing_button._badge == "OPEN")
    assert wait_for(lambda: desktop.home.drawing_button._badge == "")


def test_seven_tiles_fit_a_1080p_screen(desktop):
    from kiosk_shell import main_desktop
    tiles = desktop.home.tiles()
    assert [t.text() for t in tiles] == ["Open Games (Steam)", "My Files", "Office", "Drawing", "Antivirus Scan",
                                         "Control Center", "Shut Down System"]
    width = sum(t.width() for t in tiles) + main_desktop.px(18) * (len(tiles) - 1)
    assert width <= 1920 - 2 * main_desktop.px(56)
