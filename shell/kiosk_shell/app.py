"""Application entry point: builds the main window and shows it full screen."""

import os
import sys

from PyQt5.QtWidgets import QApplication

from kiosk_shell.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    # KIOSK_DEV=1 runs in a normal window for development on a desktop machine.
    if os.environ.get("KIOSK_DEV") == "1":
        window.resize(1280, 720)
        window.show()
    else:
        window.showFullScreen()
    return app.exec_()
