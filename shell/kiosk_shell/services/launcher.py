"""Starts Steam (or any configured program) and reports when it exits."""

import shlex

from PyQt5.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal


class AppLauncher(QObject):
    started = pyqtSignal()
    finished = pyqtSignal(int)  # exit code
    failed = pyqtSignal(str)  # could not start; human-readable reason

    def __init__(self, command: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._argv = shlex.split(command)
        self._process = QProcess(self)
        # Steam's output ends up in the shell log that kiosk-session writes.
        self._process.setProcessChannelMode(QProcess.ForwardedChannels)
        self._process.started.connect(self.started)
        self._process.finished.connect(lambda code, _status: self.finished.emit(code))
        self._process.errorOccurred.connect(self._on_error)

    def is_running(self) -> bool:
        return self._process.state() != QProcess.NotRunning

    def start(self, extra_env: dict[str, str] | None = None) -> None:
        """Start the program; extra_env is added to its environment (e.g. Competitor Mode's)."""
        if self.is_running():
            return
        if not self._argv:
            self.failed.emit("No command configured.")
            return
        env = QProcessEnvironment.systemEnvironment()
        for key, value in (extra_env or {}).items():
            env.insert(key, value)
        self._process.setProcessEnvironment(env)
        self._process.start(self._argv[0], self._argv[1:])

    def _on_error(self, error: QProcess.ProcessError) -> None:
        # A crash still emits finished(); only a failed start needs reporting here.
        if error == QProcess.FailedToStart:
            self.failed.emit(f"Could not start {self._argv[0]}: {self._process.errorString()}")
