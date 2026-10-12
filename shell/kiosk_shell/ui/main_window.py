"""Home screen: large tiles for Steam, Settings and Power (placeholders for now)."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QWidget


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Kiosk Shell")
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setStyleSheet(
            "QWidget { background: #101418; color: #e8eef2; }"
            "QPushButton { font-size: 32px; min-width: 260px; min-height: 260px;"
            "  border-radius: 24px; background: #1d252c; }"
            "QPushButton:focus { background: #2f6fde; }"
        )

        layout = QHBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(48)
        for label in ("Steam", "Settings", "Power"):
            layout.addWidget(QPushButton(label))
