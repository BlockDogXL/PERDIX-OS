"""Office and drawing apps: LibreOffice and Krita, started from the desktop and from My Files.

LibreOffice is the document engine: it opens and saves Word, Excel and PowerPoint files (.docx,
.xlsx, .pptx) as well as OpenDocument, keeping the file's own format when saving (the PERDIX
LibreOffice defaults in rootfs/usr/lib/libreoffice/share/registry/perdix.xcd switch off the
"Keep current format?" question). Krita opens drawings and images.

Each app runs as its own process on top of the full-screen desktop (Openbox stacks new windows
above it). While one runs, opening another file hands it to the running instance, and pressing the
desktop tile again brings its window back to the front (wmctrl).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from PyQt5.QtCore import QObject, QProcess, pyqtSignal


@dataclass(frozen=True)
class App:
    key: str
    name: str  # shown to the user
    program: str
    wm_class: str  # X11 window class, to bring the window back to the front
    extensions: frozenset[str]
    args: tuple[str, ...] = ()  # always passed, before the file

    def installed(self) -> bool:
        return shutil.which(self.program) is not None

    def argv(self, path: str | None = None) -> list[str]:
        # An absolute path can never be mistaken for a command-line option ("-file.xlsx").
        return [*self.args, os.path.abspath(path)] if path else list(self.args)


OFFICE = App(
    "office", "LibreOffice", "libreoffice", "libreoffice",
    frozenset({
        # Writer
        ".docx", ".doc", ".odt", ".rtf", ".txt", ".dotx", ".ott",
        # Calc
        ".xlsx", ".xls", ".xlsm", ".ods", ".csv", ".xltx", ".ots",
        # Impress
        ".pptx", ".ppt", ".odp", ".ppsx", ".pps", ".potx", ".otp",
        # Draw (PDF opens for editing in Draw)
        ".odg", ".pdf",
    }),
    ("--nologo",),
)
DRAWING = App(
    "drawing", "Krita", "krita", "krita",
    frozenset({".kra", ".ora", ".psd", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif",
               ".exr", ".svg"}),
    ("--nosplash",),
)
APPS = (OFFICE, DRAWING)


def app_for(path: str) -> App | None:
    """The app that opens this file, by its extension (case-insensitive)."""
    extension = os.path.splitext(path)[1].lower()
    return next((app for app in APPS if extension in app.extensions), None)


def raise_window(app: App) -> bool:
    """Bring the app's window to the front. False if wmctrl is missing."""
    if shutil.which("wmctrl") is None:
        return False
    return QProcess.startDetached("wmctrl", ["-x", "-a", app.wm_class])


class AppRunner(QObject):
    """One app's process. The first start is tracked (for the desktop badge); later files are
    handed to the running instance, which LibreOffice and Krita both accept."""

    started = pyqtSignal()
    finished = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, app: App, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ForwardedChannels)  # into the session log
        self._process.started.connect(self.started)
        self._process.finished.connect(lambda code, _status: self.finished.emit(code))
        self._process.errorOccurred.connect(self._on_error)

    def is_running(self) -> bool:
        return self._process.state() != QProcess.NotRunning

    def open(self, path: str | None = None) -> str:
        """Start the app (optionally with a file); returns a short status message for the user."""
        if not self.app.installed():
            self.failed.emit(f"{self.app.name} is not installed.")
            return ""
        name = os.path.basename(path) if path else ""
        if self.is_running():
            if path:
                QProcess.startDetached(self.app.program, self.app.argv(path))
                return f"Opening {name} in {self.app.name}…"
            raise_window(self.app)
            return f"{self.app.name} is already open."
        self._process.start(self.app.program, self.app.argv(path))
        return f"Opening {name} in {self.app.name}…" if path else f"Starting {self.app.name}…"

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            self.failed.emit(f"Could not start {self.app.name}: {self._process.errorString()}")
