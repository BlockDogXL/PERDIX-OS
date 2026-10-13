"""On-demand ClamAV scan (clamscan) with live progress.

Virus definitions are kept up to date by clamav-freshclam.service; the kiosk user only reads them.
"""

import glob
import shutil
from dataclasses import dataclass, field

from PyQt5.QtCore import QObject, QProcess, pyqtSignal

CLAMAV_DB_DIR = "/var/lib/clamav"


@dataclass
class ScanResult:
    files_scanned: int = 0
    threats: list[tuple[str, str]] = field(default_factory=list)  # (path, signature)
    error: str = ""
    cancelled: bool = False


class VirusScanner(QObject):
    progress = pyqtSignal(int)  # files scanned so far
    threat_found = pyqtSignal(str, str)  # path, signature
    finished = pyqtSignal(object)  # ScanResult

    def __init__(self, target: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.target = target
        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._reset()

    def _reset(self) -> None:
        self._result = ScanResult()
        self._stdout_buffer = ""
        self._stderr_lines: list[str] = []

    def is_running(self) -> bool:
        return self._process.state() != QProcess.NotRunning

    def start(self) -> None:
        if self.is_running():
            return
        self._reset()
        if shutil.which("clamscan") is None:
            self._finish_with_error("ClamAV is not installed (package 'clamav').")
            return
        if not glob.glob(f"{CLAMAV_DB_DIR}/*.c[lv]d"):
            self._finish_with_error(
                "Virus definitions have not been downloaded yet. "
                "They download automatically once the system is online."
            )
            return
        self._process.start("clamscan", ["--recursive", "--no-summary", self.target])

    def cancel(self) -> None:
        if self.is_running():
            self._result.cancelled = True
            self._process.terminate()

    def handle_line(self, line: str) -> None:
        """Parse one line of clamscan output: '<path>: OK', '<path>: <signature> FOUND', ..."""
        path, sep, status = line.rpartition(": ")
        if not sep:
            return
        self._result.files_scanned += 1
        if status.endswith(" FOUND"):
            signature = status[: -len(" FOUND")]
            self._result.threats.append((path, signature))
            self.threat_found.emit(path, signature)
        self.progress.emit(self._result.files_scanned)

    def _read_stdout(self) -> None:
        self._stdout_buffer += bytes(self._process.readAllStandardOutput()).decode(errors="replace")
        *lines, self._stdout_buffer = self._stdout_buffer.split("\n")
        for line in lines:
            self.handle_line(line)

    def _read_stderr(self) -> None:
        text = bytes(self._process.readAllStandardError()).decode(errors="replace")
        self._stderr_lines = (self._stderr_lines + text.splitlines())[-5:]

    def _on_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        if self._stdout_buffer:
            self.handle_line(self._stdout_buffer)
            self._stdout_buffer = ""
        # clamscan: 0 = clean, 1 = threats found, 2 = an error occurred (e.g. unreadable files).
        if exit_code == 2 and not self._result.cancelled:
            self._result.error = "\n".join(self._stderr_lines) or "clamscan reported an error."
        self.finished.emit(self._result)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            self._finish_with_error(f"Could not start clamscan: {self._process.errorString()}")

    def _finish_with_error(self, message: str) -> None:
        self._result.error = message
        self.finished.emit(self._result)
