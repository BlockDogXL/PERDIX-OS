"""PERDIX Control Center: the esports tuning hub of PERDIX OS.

A frameless, custom-chromed PyQt5 window (dark graphite with neon-orange and ice-blue
accents, 4 px corners). A vertical menu on the left switches the module shown on the right:

  01 Sens & Accel      Raw Accel-style curves (linear / natural / classic) on libinput, with a
                       graph; eDPI / cm-per-360 calculator and game converter
  02 Tactical Scope    crosshair designer and overlay; zoom key (hold to magnify the centre)
  03 Macro Studio      key / text / delay macros, saved as JSON, played through uinput
  04 Game Mode & HUD   ULTRA FPS / COMPETITOR MODE switch; PERDIX HUD theme engine for MangoHud
  05 Network           Zero-Ping: BBR congestion control and tc QoS (game UDP first)

Root-level changes (CPU governor, sysctl, tc) go through `pkexec /usr/local/bin/perdix-tune`.

Settings live in ~/.config/perdix/hub.json; macros in ~/.config/perdix/macros.json;
the HUD config is written to ~/.config/MangoHud/MangoHud.conf.

Run:   python -m kiosk_shell.perdix_hub          (from shell/)
       python kiosk_shell/perdix_hub.py
       python -m kiosk_shell.perdix_hub --apply-saved   (re-apply mouse settings, no window;
                                                          run from .xinitrc at session start)
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import asdict, dataclass

if __package__ in (None, ""):  # run as a plain script: make the kiosk_shell package importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QDateTime, QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QBitmap, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiosk_shell import __version__
from kiosk_shell.services import competitor, hub_settings, hud, keywatch, macros, magnifier, mouse, tuning

# --- Look -----------------------------------------------------------------------------------------

BG = "#0a0b0d"
PANEL = "#111317"
PANEL_2 = "#171a1f"
FIELD = "#0d0f12"
LINE = "#262a31"
ORANGE = "#ff6a1a"
ORANGE_HOT = "#ff8a4a"
ICE = "#8fe3ff"
TEXT = "#d9e2ea"
MUTED = "#6b7480"
DANGER = "#ff3355"
RADIUS = 4

FONT_STACK = '"Rajdhani", "Inter", "Noto Sans", "DejaVu Sans", sans-serif'
MONO_STACK = '"JetBrains Mono", "Noto Sans Mono", "DejaVu Sans Mono", monospace'

STYLESHEET = f"""
QWidget {{ color: {TEXT}; font-family: {FONT_STACK}; font-size: 14px; }}
QLabel#PageTitle {{ font-size: 30px; font-weight: 700; color: {TEXT}; }}
QLabel#PageText, QLabel#Muted {{ color: {MUTED}; }}
QLabel#Eyebrow {{ font-size: 11px; font-weight: 700; color: {ICE}; }}
QLabel#EyebrowOrange {{ font-size: 11px; font-weight: 700; color: {ORANGE}; }}
QLabel#Readout {{ font-family: {MONO_STACK}; font-size: 26px; font-weight: 600; color: {ICE}; }}
QLabel#ReadoutSmall {{ font-family: {MONO_STACK}; font-size: 18px; font-weight: 600; color: {ICE}; }}
QLabel#ReadoutOrange {{ font-family: {MONO_STACK}; font-size: 34px; font-weight: 700; color: {ORANGE}; }}
QLabel#Value {{ font-family: {MONO_STACK}; color: {ORANGE}; }}
QLabel#StatusOk {{ color: {ICE}; }}
QLabel#StatusError {{ color: {DANGER}; }}

QFrame#Card {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: {RADIUS}px; }}

QPushButton {{
    background: {PANEL_2}; border: 1px solid {LINE}; border-radius: {RADIUS}px;
    padding: 8px 16px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {ICE}; color: {ICE}; }}
QPushButton:pressed {{ background: {FIELD}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {LINE}; }}
QPushButton#Primary {{ background: {ORANGE}; color: {BG}; border: 1px solid {ORANGE}; font-weight: 700; }}
QPushButton#Primary:hover {{ background: {ORANGE_HOT}; border-color: {ORANGE_HOT}; color: {BG}; }}
QPushButton#Primary:disabled {{ background: {PANEL_2}; border-color: {LINE}; color: {MUTED}; }}
QPushButton#Segment {{ padding: 7px 12px; background: {FIELD}; }}
QPushButton#Segment:checked {{ background: rgba(255, 106, 26, 38); border-color: {ORANGE}; color: {ORANGE}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {FIELD}; border: 1px solid {LINE}; border-radius: {RADIUS}px;
    padding: 6px 10px; selection-background-color: {ORANGE}; selection-color: {BG};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ORANGE}; }}
QLineEdit[readOnly="true"] {{ color: {ICE}; font-family: {MONO_STACK}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_2}; border: 1px solid {LINE}; selection-background-color: rgba(255, 106, 26, 60);
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0; border: none;
}}

QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ORANGE}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 12px; height: 16px; margin: -6px 0; background: {ICE}; border-radius: 2px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}

QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid {LINE}; border-radius: 2px; background: {FIELD};
}}
QCheckBox::indicator:hover {{ border-color: {ICE}; }}
QCheckBox::indicator:checked {{ background: {ORANGE}; border-color: {ORANGE}; }}

QListWidget, QTableWidget {{
    background: {FIELD}; border: 1px solid {LINE}; border-radius: {RADIUS}px; outline: none;
    gridline-color: {LINE}; selection-background-color: rgba(143, 227, 255, 40); selection-color: {ICE};
}}
QHeaderView {{ background: {PANEL_2}; border: none; }}
QHeaderView::section:vertical {{
    background: {PANEL_2}; color: {MUTED}; border: none; border-right: 1px solid {LINE}; padding: 0 6px;
}}
QListWidget::item {{ padding: 9px 10px; border-left: 2px solid transparent; }}
QListWidget::item:hover {{ background: {PANEL_2}; }}
QListWidget::item:selected {{
    background: rgba(255, 106, 26, 30); color: {ORANGE}; border-left: 2px solid {ORANGE};
}}
QTableWidget::item {{ padding: 4px 8px; }}
QTableWidget::item:selected {{ background: rgba(143, 227, 255, 30); color: {ICE}; }}
QHeaderView::section {{
    background: {PANEL_2}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
    padding: 6px 8px; font-size: 11px; font-weight: 700;
}}
QTableCornerButton::section {{ background: {PANEL_2}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 2px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {ORANGE}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {ICE}; padding: 4px; }}
"""


def qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


def tracked_label(text: str, name: str = "Eyebrow", spacing: float = 2.5) -> QLabel:
    """Small uppercase label with wide letter spacing (stylesheets can't set letter spacing)."""
    label = QLabel(text.upper(), objectName=name)
    font = QFont(label.font())
    font.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    label.setFont(font)
    return label


def primary_button(text: str) -> QPushButton:
    return QPushButton(text, objectName="Primary", cursor=Qt.PointingHandCursor)


def button(text: str) -> QPushButton:
    return QPushButton(text, cursor=Qt.PointingHandCursor)


def home_path(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def set_status(label: QLabel, text: str, ok: bool) -> None:
    label.setObjectName("StatusOk" if ok else "StatusError")
    label.setText(text)
    label.style().unpolish(label)
    label.style().polish(label)


def segmented(options: list[tuple[str, str]], selected: str) -> tuple[QHBoxLayout, QButtonGroup]:
    """A row of exclusive toggle buttons. Each button carries its value in property 'value'."""
    row = QHBoxLayout()
    row.setSpacing(6)
    group = QButtonGroup(row)
    for value, text in options:
        b = QPushButton(text, objectName="Segment", checkable=True, cursor=Qt.PointingHandCursor)
        b.setProperty("value", value)
        b.setChecked(value == selected)
        group.addButton(b)
        row.addWidget(b)
    return row, group


def group_value(group: QButtonGroup) -> str:
    checked = group.checkedButton()
    return checked.property("value") if checked else ""


class Card(QFrame):
    """Panel with an orange notch in the top-left corner and an ice-blue tick bottom-right."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 18)
        self.body.setSpacing(12)
        if title:
            self.body.addWidget(tracked_label(title))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.fillRect(QRectF(0, 0, 3, 22), qcolor(ORANGE))
        p.fillRect(QRectF(0, 0, 22, 1), qcolor(ORANGE))
        w, h = self.width(), self.height()
        p.fillRect(QRectF(w - 14, h - 1, 14, 1), qcolor(ICE, 0.7))
        p.fillRect(QRectF(w - 1, h - 14, 1, 14), qcolor(ICE, 0.7))


class Readout(QWidget):
    """Caption over a big monospace number."""

    def __init__(self, caption: str, orange: bool = False, small: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(tracked_label(caption, "Muted", 1.5))
        name = "ReadoutOrange" if orange else "ReadoutSmall" if small else "Readout"
        self.value = QLabel("—", objectName=name)
        layout.addWidget(self.value)

    def set(self, text: str) -> None:
        self.value.setText(text)


class LabeledSlider(QWidget):
    """Label, slider and live value. Works on integers; `scale` turns them into the shown value."""

    def __init__(self, label: str, minimum: int, maximum: int, value: int, fmt=str, parent=None) -> None:
        super().__init__(parent)
        self._fmt = fmt
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.addWidget(QLabel(label), 0, 0)
        self.readout = QLabel(objectName="Value", alignment=Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.readout, 0, 1)
        self.slider = QSlider(Qt.Horizontal, cursor=Qt.PointingHandCursor)
        self.slider.setRange(minimum, maximum)
        self.slider.valueChanged.connect(lambda v: self.readout.setText(self._fmt(v)))
        self.slider.setValue(value)
        self.readout.setText(self._fmt(value))
        layout.addWidget(self.slider, 1, 0, 1, 2)

    @property
    def valueChanged(self):
        return self.slider.valueChanged

    def value(self) -> int:
        return self.slider.value()


class PageHeader(QWidget):
    def __init__(self, index: str, area: str, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(4)
        layout.addWidget(tracked_label(f"Module {index}  //  {area}", "EyebrowOrange"))
        title_label = QLabel(title.upper(), objectName="PageTitle")
        font = QFont(title_label.font())
        font.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        title_label.setFont(font)
        layout.addWidget(title_label)
        description = QLabel(text, objectName="PageText", wordWrap=True)
        layout.addWidget(description)

    def paintEvent(self, event) -> None:
        # Hazard stripes on the right of the header: the "industrial" touch.
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setClipRect(QRectF(self.width() - 150, 10, 150, 10))
        for i in range(12):
            x = self.width() - 150 + i * 14
            stripe = QPainterPath(QPointF(x, 20))
            stripe.lineTo(x + 10, 10)
            stripe.lineTo(x + 16, 10)
            stripe.lineTo(x + 6, 20)
            p.fillPath(stripe, qcolor(ORANGE, 0.55 if i % 2 else 0.25))


# --- Icons ----------------------------------------------------------------------------------------


def icon_path(kind: str) -> QPainterPath:
    """Line icons in a 100x100 box centred on (0, 0)."""
    p = QPainterPath()
    if kind == "mouse":
        p.addRoundedRect(QRectF(-24, -40, 48, 80), 24, 24)
        p.moveTo(0, -40); p.lineTo(0, -12)
        p.moveTo(-24, -12); p.lineTo(24, -12)
    elif kind == "scope":
        p.addEllipse(QPointF(0, 0), 34, 34)
        p.addEllipse(QPointF(0, 0), 4, 4)
        for x1, y1, x2, y2 in ((0, -46, 0, -18), (0, 18, 0, 46), (-46, 0, -18, 0), (18, 0, 46, 0)):
            p.moveTo(x1, y1); p.lineTo(x2, y2)
    elif kind == "macro":
        p.addRoundedRect(QRectF(-44, -26, 88, 52), 6, 6)
        for x in (-28, -12, 4, 20):
            p.addRect(QRectF(x, -14, 8, 8))
        p.moveTo(-22, 12); p.lineTo(22, 12)
    elif kind == "hud":
        p.arcMoveTo(QRectF(-42, -38, 84, 84), 200)
        p.arcTo(QRectF(-42, -38, 84, 84), 200, -220)
        p.moveTo(0, 4); p.lineTo(24, -18)
        p.addEllipse(QPointF(0, 4), 5, 5)
        p.moveTo(-30, 34); p.lineTo(30, 34)
    elif kind == "net":
        for r in (14, 30, 46):
            p.arcMoveTo(QRectF(-r, 30 - r, 2 * r, 2 * r), 45)
            p.arcTo(QRectF(-r, 30 - r, 2 * r, 2 * r), 45, 90)
        p.addEllipse(QPointF(0, 30), 4, 4)
    elif kind == "bolt":
        p.moveTo(8, -46); p.lineTo(-24, 6); p.lineTo(0, 6); p.lineTo(-8, 46)
        p.lineTo(24, -6); p.lineTo(0, -6); p.closeSubpath()
    return p


def draw_icon(p: QPainter, kind: str, center: QPointF, size: float, color: QColor, width: float = 7) -> None:
    p.save()
    p.translate(center)
    p.scale(size / 100, size / 100)
    p.strokePath(icon_path(kind), QPen(color, width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
    p.restore()


# --- Window chrome --------------------------------------------------------------------------------


class ChromeButton(QPushButton):
    def __init__(self, glyph: str, parent=None) -> None:
        super().__init__(parent)
        self.glyph = glyph
        self.setFixedSize(40, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse()
        if hover:
            p.fillRect(self.rect(), qcolor(DANGER if self.glyph == "close" else LINE))
        p.setPen(QPen(qcolor(TEXT if hover else MUTED), 1.4))
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.glyph == "min":
            p.drawLine(QPointF(c.x() - 6, c.y() + 4), QPointF(c.x() + 6, c.y() + 4))
        elif self.glyph == "max":
            p.drawRect(QRectF(c.x() - 6, c.y() - 5, 12, 10))
        else:
            p.drawLine(QPointF(c.x() - 5, c.y() - 5), QPointF(c.x() + 5, c.y() + 5))
            p.drawLine(QPointF(c.x() + 5, c.y() - 5), QPointF(c.x() - 5, c.y() + 5))

    def enterEvent(self, event) -> None:
        self.update()

    def leaveEvent(self, event) -> None:
        self.update()


class TitleBar(QWidget):
    """Custom title bar: Piri + name on the left, version tag and window buttons on the right.
    Drag it to move the window; double-click to maximise."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self.setFixedHeight(48)
        self._icon = QImage(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "resources", "brand", "piri-silhouette-mask.png"))
        if not self._icon.isNull():
            self._icon = self._icon.convertToFormat(QImage.Format_ARGB32_Premultiplied).scaled(
                26, 26, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            tint = QPainter(self._icon)
            tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
            tint.fillRect(self._icon.rect(), qcolor(ORANGE))
            tint.end()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(56 if not self._icon.isNull() else 18, 0, 6, 0)
        layout.setSpacing(10)
        brand = tracked_label("PERDIX", "EyebrowOrange", 4)
        brand.setStyleSheet("font-size: 15px;")
        layout.addWidget(brand)
        layout.addWidget(tracked_label("Control Center", "Eyebrow", 3))
        layout.addStretch(1)
        layout.addWidget(tracked_label(f"v{__version__}  //  online", "Muted", 2))
        layout.addSpacing(12)
        self.minimize = ChromeButton("min")
        self.maximize = ChromeButton("max")
        self.close = ChromeButton("close")
        for b in (self.minimize, self.maximize, self.close):
            layout.addWidget(b)
        self.minimize.clicked.connect(window.showMinimized)
        self.maximize.clicked.connect(self.toggle_maximized)
        self.close.clicked.connect(window.close)

    def toggle_maximized(self) -> None:
        self._window.showNormal() if self._window.isMaximized() else self._window.showMaximized()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                return
            self._drag_from = event.globalPos() - self._window.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if getattr(self, "_drag_from", None) is not None and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPos() - self._drag_from)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.toggle_maximized()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(PANEL))
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), qcolor(LINE))
        p.fillRect(QRectF(0, self.height() - 2, 236, 2), qcolor(ORANGE))  # accent under the brand
        if not self._icon.isNull():
            p.drawImage(18, (self.height() - self._icon.height()) // 2, self._icon)


class NavButton(QPushButton):
    """Menu entry: index, icon, title and subtitle. Checked = orange bar and tint."""

    def __init__(self, index: str, icon: str, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        self.index, self.icon, self.title, self.subtitle = index, icon, title, subtitle
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(68)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def enterEvent(self, event) -> None:
        self.update()

    def leaveEvent(self, event) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        active, hover = self.isChecked(), self.underMouse() or self.hasFocus()
        if active:
            fill = QLinearGradient(0, 0, self.width(), 0)
            fill.setColorAt(0, qcolor(ORANGE, 0.18))
            fill.setColorAt(1, qcolor(ORANGE, 0.0))
            p.fillRect(self.rect(), fill)
            p.fillRect(QRectF(0, 0, 3, self.height()), qcolor(ORANGE))
        elif hover:
            p.fillRect(self.rect(), qcolor(PANEL_2))
            p.fillRect(QRectF(0, 0, 2, self.height()), qcolor(ICE, 0.8))
        accent = qcolor(ORANGE) if active else qcolor(ICE) if hover else qcolor(MUTED)

        mono = QFont(self.font())
        mono.setFamily("DejaVu Sans Mono")
        mono.setPixelSize(11)
        p.setFont(mono)
        p.setPen(accent)
        p.drawText(QRectF(18, 0, 24, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.index)
        draw_icon(p, self.icon, QPointF(62, self.height() / 2), 24, accent)

        title = QFont(self.font())
        title.setPixelSize(14)
        title.setWeight(QFont.Bold)
        title.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        p.setFont(title)
        p.setPen(qcolor(ORANGE if active else TEXT))
        p.drawText(QRectF(88, 14, self.width() - 96, 20), Qt.AlignLeft | Qt.AlignVCenter, self.title.upper())
        sub = QFont(self.font())
        sub.setPixelSize(12)
        p.setFont(sub)
        p.setPen(qcolor(MUTED))
        p.drawText(QRectF(88, 34, self.width() - 96, 18), Qt.AlignLeft | Qt.AlignVCenter, self.subtitle)


class Sidebar(QWidget):
    def __init__(self, entries: list[tuple[str, str, str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(272)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 22, 0, 18)
        layout.setSpacing(4)
        heading = tracked_label("Navigation", "Muted", 3)
        heading.setContentsMargins(18, 0, 0, 10)
        layout.addWidget(heading)
        self.group = QButtonGroup(self)
        self.buttons = []
        for i, entry in enumerate(entries):
            b = NavButton(*entry)
            self.group.addButton(b, i)
            self.buttons.append(b)
            layout.addWidget(b)
        layout.addStretch(1)
        footer = tracked_label("PERDIX OS  //  Esports", "Muted", 2)
        footer.setContentsMargins(18, 0, 0, 0)
        layout.addWidget(footer)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(PANEL))
        p.fillRect(QRectF(self.width() - 1, 0, 1, self.height()), qcolor(LINE))
        # Hazard stripes at the bottom of the menu.
        p.setRenderHint(QPainter.Antialiasing)
        y = self.height() - 54
        p.setClipRect(QRectF(18, y, self.width() - 36, 8))
        for i in range(30):
            x = 18 + i * 12
            stripe = QPainterPath(QPointF(x, y + 8))
            stripe.lineTo(x + 8, y)
            stripe.lineTo(x + 13, y)
            stripe.lineTo(x + 5, y + 8)
            p.fillPath(stripe, qcolor(ORANGE, 0.35))


class StatusBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 0, 0)
        layout.setSpacing(24)
        self.gamemode = tracked_label("", "Muted", 1.5)
        self.mangohud = tracked_label("", "Muted", 1.5)
        self.xinput = tracked_label("", "Muted", 1.5)
        for label in (self.gamemode, self.mangohud, self.xinput):
            layout.addWidget(label)
        layout.addStretch(1)
        self.clock = tracked_label("", "Eyebrow", 1.5)
        layout.addWidget(self.clock)
        layout.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)
        self._timer = QTimer(self, interval=5000, timeout=self.refresh)
        self._timer.start()
        self._clock_timer = QTimer(self, interval=1000, timeout=self._tick)
        self._clock_timer.start()
        self.refresh()
        self._tick()

    def _tick(self) -> None:
        self.clock.setText(QDateTime.currentDateTime().toString("HH:mm:ss"))

    def refresh(self) -> None:
        self.gamemode.setText(f"■ GameMode {hud.gamemode_status()}".upper())
        self.mangohud.setText(("■ MangoHud ready" if hud.mangohud_installed() else "■ MangoHud missing").upper())
        self.xinput.setText(("■ xinput ok" if mouse.XInput().available else "■ xinput missing").upper())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(PANEL))
        p.fillRect(QRectF(0, 0, self.width(), 1), qcolor(LINE))


# --- 01 Sens & Accel ------------------------------------------------------------------------------


class Worker(QThread):
    """Runs a blocking function (pkexec, subprocess) off the UI thread; emits its result."""

    done = pyqtSignal(object)

    def __init__(self, func, parent=None) -> None:
        super().__init__(parent)
        self.func = func

    def run(self) -> None:
        try:
            result = self.func()
        except Exception as error:  # shown in the UI instead of killing the thread silently
            result = error
        self.done.emit(result)


class AccelGraph(QWidget):
    """Sensitivity multiplier against hand speed for the selected curve (drawn with QPainter)."""

    Y_MAX = 4.5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.curve, self.amount = "off", 0.5
        self.setMinimumHeight(210)

    def set_curve(self, curve: str, amount: float) -> None:
        self.curve, self.amount = curve, amount
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qcolor(FIELD))
        p.setPen(QPen(qcolor(LINE), 1))
        p.drawRect(QRectF(self.rect()).adjusted(0, 0, -1, -1))
        plot = QRectF(self.rect()).adjusted(46, 14, -16, -34)

        def point(speed: float, mult: float) -> QPointF:
            return QPointF(plot.left() + plot.width() * speed / mouse.MAX_SPEED,
                           plot.bottom() - plot.height() * min(mult, self.Y_MAX) / self.Y_MAX)

        mono = QFont("DejaVu Sans Mono")
        mono.setPixelSize(10)
        p.setFont(mono)
        for speed in range(0, int(mouse.MAX_SPEED) + 1, 10):
            x = point(speed, 0).x()
            p.setPen(QPen(qcolor(ICE, 0.07), 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            p.setPen(qcolor(MUTED))
            p.drawText(QRectF(x - 20, plot.bottom() + 4, 40, 14), Qt.AlignCenter, str(speed))
        for mult in range(0, int(self.Y_MAX) + 1):
            y = point(0, mult).y()
            p.setPen(QPen(qcolor(ICE, 0.07), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(qcolor(MUTED))
            p.drawText(QRectF(plot.left() - 42, y - 7, 36, 14), Qt.AlignRight | Qt.AlignVCenter, f"{mult}×")
        p.drawText(QRectF(plot.left(), plot.bottom() + 17, plot.width(), 14), Qt.AlignCenter,
                   "HAND SPEED  (COUNTS / MS)")

        # 1x reference line (no acceleration).
        p.setPen(QPen(qcolor(MUTED, 0.8), 1, Qt.DashLine))
        p.drawLine(point(0, 1), point(mouse.MAX_SPEED, 1))

        samples = [i * mouse.MAX_SPEED / 120 for i in range(121)]
        curve = QPainterPath(point(0, mouse.multiplier(self.curve, self.amount, 0)))
        for speed in samples[1:]:
            curve.lineTo(point(speed, mouse.multiplier(self.curve, self.amount, speed)))
        area = QPainterPath(curve)
        area.lineTo(point(mouse.MAX_SPEED, 0))
        area.lineTo(point(0, 0))
        area.closeSubpath()
        fill = QLinearGradient(plot.topLeft(), plot.bottomLeft())
        fill.setColorAt(0, qcolor(ORANGE, 0.28))
        fill.setColorAt(1, qcolor(ORANGE, 0.0))
        p.fillPath(area, fill)
        p.strokePath(curve, QPen(qcolor(ORANGE if self.curve != "off" else ICE), 2.2))


class SensPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.xinput = mouse.XInput()
        saved = hub_settings.merged({"device": "", "curve": "off", "amount": 0.35},
                                    hub_settings.load().get("mouse"))
        if saved["curve"] not in mouse.CURVES:
            saved["curve"] = "off"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(PageHeader("01", "Input", "Sens & Accel",
                                    "Raw Accel-style curves on libinput: pick a curve, set the amount, "
                                    "and it is applied to your mouse as you change it."))
        columns = QHBoxLayout()
        columns.setSpacing(18)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(18)
        right.setSpacing(18)
        columns.addLayout(left, 6)
        columns.addLayout(right, 5)
        layout.addLayout(columns, 1)

        accel = Card("Acceleration curve")
        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Device"))
        self.device = QComboBox(cursor=Qt.PointingHandCursor)
        self.device.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        device_row.addWidget(self.device, 1)
        rescan = button("Rescan")
        rescan.clicked.connect(self.load_devices)
        device_row.addWidget(rescan)
        accel.body.addLayout(device_row)
        curve_row = QHBoxLayout()
        curve_row.addWidget(QLabel("Curve"))
        self.curve = QComboBox(cursor=Qt.PointingHandCursor)
        for key, label in mouse.CURVES.items():
            self.curve.addItem(label, key)
        self.curve.setCurrentIndex(list(mouse.CURVES).index(saved["curve"]))
        curve_row.addWidget(self.curve, 1)
        accel.body.addLayout(curve_row)
        self.amount = LabeledSlider("Acceleration amount", 0, 100, round(saved["amount"] * 100),
                                    lambda v: f"{v} %")
        accel.body.addWidget(self.amount)
        self.graph = AccelGraph()
        accel.body.addWidget(self.graph, 1)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        accel.body.addWidget(self.status)
        left.addWidget(accel, 1)

        calc_card = Card("Sensitivity calculator")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.dpi = QSpinBox(minimum=100, maximum=32000, singleStep=50, value=800)
        self.game = QComboBox(cursor=Qt.PointingHandCursor)
        self.game.addItems(mouse.GAMES)
        self.sens = QDoubleSpinBox(minimum=0.001, maximum=100.0, decimals=3, singleStep=0.05, value=1.0)
        for r, (text, widget) in enumerate((("Mouse DPI", self.dpi), ("Game", self.game), ("In-game sens", self.sens))):
            form.addWidget(QLabel(text), r, 0)
            form.addWidget(widget, r, 1)
        calc_card.body.addLayout(form)
        readouts = QHBoxLayout()
        self.edpi = Readout("eDPI")
        self.cm360 = Readout("cm / 360°")
        self.in360 = Readout("in / 360°")
        for r in (self.edpi, self.cm360, self.in360):
            readouts.addWidget(r)
        calc_card.body.addLayout(readouts)
        right.addWidget(calc_card)

        convert_card = Card("Convert to another game")
        convert_row = QHBoxLayout()
        convert_row.addWidget(QLabel("Target game"))
        self.target = QComboBox(cursor=Qt.PointingHandCursor)
        self.target.addItems(mouse.GAMES)
        self.target.setCurrentText("Valorant")
        convert_row.addWidget(self.target, 1)
        convert_card.body.addLayout(convert_row)
        self.converted = Readout("Matching sensitivity", orange=True)
        convert_card.body.addWidget(self.converted)
        right.addWidget(convert_card)
        right.addStretch(1)

        for signal in (self.dpi.valueChanged, self.sens.valueChanged, self.game.currentTextChanged,
                       self.target.currentTextChanged):
            signal.connect(self.recalculate)
        self.recalculate()

        # Changes are applied (and saved) 350 ms after the last one, so dragging the slider
        # doesn't run xinput dozens of times a second.
        self._apply_timer = QTimer(self, singleShot=True, interval=350, timeout=self.apply)
        self.curve.currentIndexChanged.connect(self.curve_changed)
        self.amount.valueChanged.connect(self.curve_changed)
        self._saved_device = saved["device"]
        self.load_devices()
        self.device.currentIndexChanged.connect(lambda _i: self._apply_timer.start())
        self.curve_changed(schedule=False)

    def curve_changed(self, *_args, schedule: bool = True) -> None:
        curve = self.curve.currentData()
        self.amount.setEnabled(curve != "off")
        self.graph.set_curve(curve, self.amount.value() / 100)
        if schedule:
            set_status(self.status, "Applying…", True)
            self._apply_timer.start()

    def load_devices(self) -> None:
        self.device.blockSignals(True)
        self.device.clear()
        pointers = self.xinput.pointers()
        for pointer in pointers:
            self.device.addItem(pointer.name, pointer.id)
        if self._saved_device:
            self.device.setCurrentText(self._saved_device)
        if not self.xinput.available:
            self.device.addItem("xinput not installed")
            set_status(self.status, "Install 'xorg-xinput' to change acceleration.", False)
        elif not pointers:
            self.device.addItem("No pointer devices found")
        self.device.setEnabled(bool(pointers))
        self.device.blockSignals(False)

    def apply(self) -> None:
        pointer_id = self.device.currentData()
        curve, amount = self.curve.currentData(), self.amount.value() / 100
        if pointer_id is None:
            set_status(self.status, "No mouse to apply the curve to.", False)
            return
        ok, message = self.xinput.apply_curve(pointer_id, curve, amount)
        set_status(self.status, message, ok)
        if ok:  # re-applied at every login by `perdix_hub --apply-saved` (see .xinitrc)
            hub_settings.save_section("mouse", {"device": self.device.currentText(),
                                                "curve": curve, "amount": amount})

    def recalculate(self) -> None:
        dpi, sens = self.dpi.value(), self.sens.value()
        yaw = mouse.GAMES[self.game.currentText()]
        cm = mouse.cm_per_360(dpi, sens, yaw)
        self.edpi.set(f"{mouse.edpi(dpi, sens):,.0f}")
        self.cm360.set(f"{cm:.1f}")
        self.in360.set(f"{cm / 2.54:.1f}")
        converted = mouse.convert_sens(sens, yaw, mouse.GAMES[self.target.currentText()])
        self.converted.set(f"{converted:.3f}")


# --- 02 Tactical Scope ----------------------------------------------------------------------------


@dataclass
class Crosshair:
    style: str = "cross"  # cross, t, dot, circle
    size: int = 8
    thickness: int = 2
    gap: int = 4
    color: str = ICE
    opacity: int = 100  # percent
    outline: bool = True
    dot: bool = False


CROSSHAIR_COLORS = [ICE, ORANGE, "#3dff7a", "#ff3df2", "#ffe03d", "#ffffff"]


def crosshair_shapes(c: Crosshair, center: QPointF, scale: float = 1.0) -> tuple[list[QRectF], list[QRectF], list[QRectF]]:
    """(filled bars, filled dots, circle outlines) for a crosshair centred on `center`."""
    s, t, g = c.size * scale, max(1.0, c.thickness * scale), c.gap * scale
    x, y = center.x(), center.y()
    bars, dots, rings = [], [], []
    if c.style in ("cross", "t"):
        bars += [QRectF(x - g - s, y - t / 2, s, t), QRectF(x + g, y - t / 2, s, t),
                 QRectF(x - t / 2, y + g, t, s)]
        if c.style == "cross":
            bars.append(QRectF(x - t / 2, y - g - s, t, s))
    if c.style == "circle":
        r = g + s / 2
        rings.append(QRectF(x - r, y - r, 2 * r, 2 * r))
    if c.style == "dot" or c.dot:
        d = max(2.0, t * 1.5) if c.style != "dot" else max(2.0, s / 2)
        dots.append(QRectF(x - d / 2, y - d / 2, d, d))
    return bars, dots, rings


def paint_crosshair(p: QPainter, c: Crosshair, center: QPointF, scale: float = 1.0) -> None:
    bars, dots, rings = crosshair_shapes(c, center, scale)
    color = qcolor(c.color, c.opacity / 100)
    t = max(1.0, c.thickness * scale)
    p.setRenderHint(QPainter.Antialiasing, False)  # crisp, pixel-aligned like an in-game crosshair
    if c.outline:
        black = QColor(0, 0, 0, int(255 * c.opacity / 100))
        for r in bars + dots:
            p.fillRect(r.adjusted(-scale, -scale, scale, scale), black)
        p.setPen(QPen(black, t + 2 * scale))
        p.setBrush(Qt.NoBrush)
        for r in rings:
            p.drawEllipse(r)
    for r in bars:
        p.fillRect(r, color)
    p.setRenderHint(QPainter.Antialiasing, True)
    for r in dots:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRect(r) if c.style != "dot" else p.drawEllipse(r)
    p.setPen(QPen(color, t))
    p.setBrush(Qt.NoBrush)
    for r in rings:
        p.drawEllipse(r)


class CrosshairPreview(QWidget):
    """A dark 'range' scene with the crosshair drawn 3x so the details are visible."""

    def __init__(self, crosshair: Crosshair, parent=None) -> None:
        super().__init__(parent)
        self.crosshair = crosshair
        self.setMinimumSize(320, 260)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        sky = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        sky.setColorAt(0, QColor("#1b2027"))
        sky.setColorAt(0.55, QColor("#252a31"))
        sky.setColorAt(0.551, QColor("#141619"))
        sky.setColorAt(1, QColor("#0c0d0f"))
        p.fillRect(rect, sky)
        horizon = rect.height() * 0.55
        p.setPen(QPen(qcolor(ICE, 0.10), 1))
        vanish = QPointF(rect.width() / 2, horizon)
        for i in range(-8, 9):
            p.drawLine(vanish, QPointF(rect.width() / 2 + i * rect.width() / 6, rect.height()))
        for k in range(1, 7):
            y = horizon + (rect.height() - horizon) * (k / 6) ** 2
            p.drawLine(QPointF(0, y), QPointF(rect.width(), y))
        # A target block on the horizon to aim at.
        p.fillRect(QRectF(rect.width() / 2 + 60, horizon - 70, 34, 70), QColor("#2f353d"))
        p.fillRect(QRectF(rect.width() / 2 + 60, horizon - 70, 34, 3), qcolor(ORANGE, 0.6))
        paint_crosshair(p, self.crosshair, QPointF(round(rect.width() / 2), round(rect.height() / 2)), 3.0)
        p.setPen(qcolor(MUTED))
        p.drawText(rect.adjusted(10, 8, -10, -8), Qt.AlignRight | Qt.AlignTop, "PREVIEW  ×3")
        p.setPen(QPen(qcolor(LINE), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect.adjusted(0, 0, -1, -1))


class CrosshairOverlay(QWidget):
    """The crosshair drawn at the centre of the screen, on top of everything, ignoring input.

    The window is shaped to the crosshair pixels (X11 shape mask), so it needs no compositor
    and never blocks clicks. It re-raises itself every second in case a game covers it.
    """

    def __init__(self, crosshair: Crosshair) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.X11BypassWindowManagerHint | Qt.WindowTransparentForInput)
        self.crosshair = crosshair
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(160, 160)
        self._raise_timer = QTimer(self, interval=1000, timeout=self.raise_)

    def refresh(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)
        image = QImage(self.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        p = QPainter(image)
        paint_crosshair(p, self.crosshair, QPointF(self.width() // 2, self.height() // 2))
        p.end()
        self.setMask(QBitmap.fromImage(image.createAlphaMask()))
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._raise_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._raise_timer.stop()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        paint_crosshair(p, self.crosshair, QPointF(self.width() // 2, self.height() // 2))


class ColorSwatch(QPushButton):
    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(color)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.fillRect(r, QColor(self.color))
        if self.isChecked() or self.underMouse():
            p.setPen(QPen(qcolor(TEXT if self.isChecked() else MUTED), 2))
            p.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))


class KeyWatchThread(QThread):
    """Watches input devices with evdev. bind=True: report the first key pressed, then stop."""

    pressed = pyqtSignal(int, bool)

    def __init__(self, code: int | None, parent=None) -> None:
        super().__init__(parent)
        self.code = code
        self.stop_requested = False
        self.error = ""

    def run(self) -> None:
        def on_event(code: int, down: bool) -> None:
            self.pressed.emit(code, down)
            if self.code is None and down:
                self.stop_requested = True
        try:
            keywatch.watch(on_event, lambda: self.stop_requested, self.code)
        except Exception as error:  # permission problems etc. are shown in the UI
            self.error = str(error)


class ZoomCard(Card):
    """Zoom key: hold it to magnify the centre of the screen; release to go back."""

    DEFAULT_CODE = 275  # BTN_SIDE, the back thumb button on most gaming mice

    def __init__(self, parent=None) -> None:
        super().__init__("Zoom key", parent)
        saved = hub_settings.merged({"code": self.DEFAULT_CODE, "factor": 2.0}, hub_settings.load().get("zoom"))
        self.code = saved["code"]
        self.factor = saved["factor"] if saved["factor"] in magnifier.FACTORS else 2.0
        self.backend = magnifier.detect()
        self.watcher: KeyWatchThread | None = None
        self.binder: KeyWatchThread | None = None

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Key"))
        self.key_label = QLabel(self._name(self.code), objectName="Value")
        key_row.addWidget(self.key_label, 1)
        self.bind_button = button("Bind key…")
        self.bind_button.clicked.connect(self.start_binding)
        key_row.addWidget(self.bind_button)
        self.body.addLayout(key_row)

        factor_row, self.factor_group = segmented([(str(f), f"{f:g}×") for f in magnifier.FACTORS], str(self.factor))
        self.factor_group.buttonClicked.connect(self.factor_changed)
        self.body.addLayout(factor_row)

        actions = QHBoxLayout()
        self.enable = primary_button("Enable zoom key")
        self.enable.setCheckable(True)
        self.enable.toggled.connect(self.toggle_watch)
        actions.addWidget(self.enable)
        actions.addStretch(1)
        self.body.addLayout(actions)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        self.body.addWidget(self.status)

        ok, reason = keywatch.evdev_available()
        if self.backend is None:
            ok, reason = False, "No zoom backend: needs X11 with xrandr, or Hyprland."
        for widget in (self.enable, self.bind_button):
            widget.setEnabled(ok)
        if ok:
            set_status(self.status, f"Backend: {self.backend.name}. Hold the key in game to zoom.", True)
        else:
            set_status(self.status, reason, False)

    FRIENDLY = {272: "Mouse 1", 273: "Mouse 2", 274: "Mouse 3 (wheel)", 275: "Mouse 4 (back)",
                276: "Mouse 5 (forward)"}

    @classmethod
    def _name(cls, code: int) -> str:
        if code in cls.FRIENDLY:
            return cls.FRIENDLY[code]
        try:
            return keywatch.key_name(code).removeprefix("KEY_")
        except ImportError:
            return f"Key code {code}"

    def save(self) -> None:
        hub_settings.save_section("zoom", {"code": self.code, "factor": self.factor})

    def factor_changed(self, *_args) -> None:
        self.factor = float(group_value(self.factor_group))
        self.save()

    def start_binding(self) -> None:
        if self.enable.isChecked():
            self.enable.setChecked(False)
        self.bind_button.setEnabled(False)
        self.key_label.setText("Press a key or mouse button…")
        self.binder = KeyWatchThread(None, self)
        self.binder.pressed.connect(self._bound)
        self.binder.finished.connect(lambda: self.bind_button.setEnabled(True))
        self.binder.start()

    def _bound(self, code: int, down: bool) -> None:
        if down:
            self.code = code
            self.key_label.setText(self._name(code))
            self.save()

    def toggle_watch(self, on: bool) -> None:
        if on:
            self.watcher = KeyWatchThread(self.code, self)
            self.watcher.pressed.connect(self._zoom)
            self.watcher.finished.connect(self._watch_ended)
            self.watcher.start()
            self.enable.setText("Disable zoom key")
            set_status(self.status, f"Hold {self._name(self.code)} to zoom {self.factor:g}×.", True)
        elif self.watcher is not None:
            self.watcher.stop_requested = True
            self.enable.setText("Enable zoom key")

    def _zoom(self, _code: int, down: bool) -> None:
        if self.backend is None:
            return
        ok = self.backend.zoom(self.factor) if down else self.backend.reset()
        if not ok:
            set_status(self.status, f"{self.backend.name} refused the zoom.", False)

    def _watch_ended(self) -> None:
        if self.backend is not None:
            self.backend.reset()  # never leave the screen zoomed
        if self.watcher is not None and self.watcher.error:
            set_status(self.status, f"Key watch stopped: {self.watcher.error}", False)
        if self.enable.isChecked():
            self.enable.blockSignals(True)
            self.enable.setChecked(False)
            self.enable.setText("Enable zoom key")
            self.enable.blockSignals(False)

    def shutdown(self) -> None:
        for thread in (self.watcher, self.binder):
            if thread is not None and thread.isRunning():
                thread.stop_requested = True
                thread.wait(1000)


class ScopePage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        saved = hub_settings.merged(asdict(Crosshair()), hub_settings.load().get("crosshair"))
        self.crosshair = Crosshair(**saved)
        self.overlay = CrosshairOverlay(self.crosshair)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(PageHeader("02", "Optics", "Tactical Scope",
                                    "Design your crosshair and pin it to the centre of the screen, and set "
                                    "a zoom key. Many games and leagues ban overlays and zoom tools in "
                                    "ranked play: check the rules first."))
        columns = QHBoxLayout()
        columns.setSpacing(18)
        layout.addLayout(columns, 1)

        reticle = Card("Reticle")
        style_row, self.style_group = segmented(
            [("cross", "Cross"), ("t", "T-shape"), ("dot", "Dot"), ("circle", "Circle")], self.crosshair.style)
        reticle.body.addLayout(style_row)
        self.size = LabeledSlider("Length", 2, 40, self.crosshair.size, lambda v: f"{v} px")
        self.thickness = LabeledSlider("Thickness", 1, 8, self.crosshair.thickness, lambda v: f"{v} px")
        self.gap = LabeledSlider("Gap", 0, 20, self.crosshair.gap, lambda v: f"{v} px")
        self.opacity = LabeledSlider("Opacity", 20, 100, self.crosshair.opacity, lambda v: f"{v} %")
        for slider in (self.size, self.thickness, self.gap, self.opacity):
            reticle.body.addWidget(slider)
            slider.valueChanged.connect(self.update_crosshair)
        reticle.body.addWidget(tracked_label("Colour", "Muted", 1.5))
        swatches = QHBoxLayout()
        swatches.setSpacing(4)
        self.color_group = QButtonGroup(self)
        for color in CROSSHAIR_COLORS:
            swatch = ColorSwatch(color)
            swatch.setChecked(color.lower() == self.crosshair.color.lower())
            self.color_group.addButton(swatch)
            swatches.addWidget(swatch)
        swatches.addStretch(1)
        reticle.body.addLayout(swatches)
        toggles = QHBoxLayout()
        self.outline = QCheckBox("Black outline", checked=self.crosshair.outline)
        self.dot = QCheckBox("Centre dot", checked=self.crosshair.dot)
        toggles.addWidget(self.outline)
        toggles.addWidget(self.dot)
        toggles.addStretch(1)
        reticle.body.addLayout(toggles)
        reticle.body.addStretch(1)
        columns.addWidget(reticle, 5)

        preview_card = Card("Live preview")
        self.preview = CrosshairPreview(self.crosshair)
        preview_card.body.addWidget(self.preview, 1)
        actions = QHBoxLayout()
        self.overlay_button = primary_button("Show on screen")
        self.overlay_button.setCheckable(True)
        self.overlay_button.toggled.connect(self.toggle_overlay)
        save = button("Save crosshair")
        save.clicked.connect(self.save)
        actions.addWidget(self.overlay_button)
        actions.addWidget(save)
        actions.addStretch(1)
        preview_card.body.addLayout(actions)
        self.status = QLabel(objectName="Muted")
        preview_card.body.addWidget(self.status)
        right = QVBoxLayout()
        right.setSpacing(18)
        right.addWidget(preview_card, 1)
        self.zoom = ZoomCard()
        right.addWidget(self.zoom)
        columns.addLayout(right, 6)

        self.style_group.buttonClicked.connect(self.update_crosshair)
        self.color_group.buttonClicked.connect(self.update_crosshair)
        self.outline.toggled.connect(self.update_crosshair)
        self.dot.toggled.connect(self.update_crosshair)

    def update_crosshair(self, *_args) -> None:
        c = self.crosshair
        c.style = group_value(self.style_group) or c.style
        c.size, c.thickness, c.gap, c.opacity = (self.size.value(), self.thickness.value(),
                                                 self.gap.value(), self.opacity.value())
        checked = self.color_group.checkedButton()
        c.color = checked.color if checked else c.color
        c.outline, c.dot = self.outline.isChecked(), self.dot.isChecked()
        self.preview.update()
        if self.overlay.isVisible():
            self.overlay.refresh()

    def toggle_overlay(self, on: bool) -> None:
        if on:
            self.overlay.refresh()
            self.overlay.show()
            self.overlay_button.setText("Hide from screen")
        else:
            self.overlay.hide()
            self.overlay_button.setText("Show on screen")

    def save(self) -> None:
        hub_settings.save_section("crosshair", asdict(self.crosshair))
        set_status(self.status, "Crosshair saved.", True)


# --- 03 Macro Studio ------------------------------------------------------------------------------


class MacroRunner(QThread):
    done = pyqtSignal(str)

    def __init__(self, macro: macros.Macro, parent=None) -> None:
        super().__init__(parent)
        self.macro = macro
        self.stop_requested = False

    def run(self) -> None:
        try:
            macros.play(self.macro, should_stop=lambda: self.stop_requested)
            self.done.emit("Stopped." if self.stop_requested else f"Played '{self.macro.name}'.")
        except Exception as error:  # report any device/permission problem in the UI
            self.done.emit(f"Playback failed: {error}")


STEP_LABELS = {"key": "KEY COMBO", "text": "TYPE TEXT", "delay": "DELAY (MS)"}
STEP_DEFAULTS = {"key": "ctrl+c", "text": "gg", "delay": "100"}


class MacroPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.macros = macros.load_macros()
        self.runner: MacroRunner | None = None
        self.countdown = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(PageHeader("03", "Automation", "Macro Studio",
                                    "Chain key combos, typed text and delays. Runs on a virtual keyboard "
                                    "after a 3-second countdown, so you can switch to the target window."))
        columns = QHBoxLayout()
        columns.setSpacing(18)
        layout.addLayout(columns, 1)

        library = Card("Library")
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.show_macro)
        library.body.addWidget(self.list, 1)
        lib_actions = QHBoxLayout()
        new = button("New")
        new.clicked.connect(self.new_macro)
        self.delete_button = button("Delete")
        self.delete_button.clicked.connect(self.delete_macro)
        lib_actions.addWidget(new)
        lib_actions.addWidget(self.delete_button)
        library.body.addLayout(lib_actions)
        columns.addWidget(library, 3)

        editor = Card("Sequence editor")
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self.name = QLineEdit(placeholderText="e.g. Buy loadout")
        name_row.addWidget(self.name, 1)
        editor.body.addLayout(name_row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["ACTION", "VALUE"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setStyleSheet(f"color: {MUTED};")
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        editor.body.addWidget(self.table, 1)

        step_row = QHBoxLayout()
        for kind, text in (("key", "+ Key combo"), ("text", "+ Text"), ("delay", "+ Delay")):
            b = button(text)
            b.clicked.connect(lambda _checked, k=kind: self.add_step(k))
            step_row.addWidget(b)
        step_row.addStretch(1)
        for text, handler in (("▲", lambda: self.move_step(-1)), ("▼", lambda: self.move_step(1)),
                              ("Remove", self.remove_step)):
            b = button(text)
            b.clicked.connect(handler)
            step_row.addWidget(b)
        editor.body.addLayout(step_row)
        editor.body.addWidget(QLabel("Key combos: ctrl+shift+r, alt+tab, f5, enter… "
                                     "Text: US keyboard layout.", objectName="Muted"))

        run_row = QHBoxLayout()
        save = primary_button("Save macro")
        save.clicked.connect(self.save_macro)
        self.run_button = button("Run in 3 s")
        self.run_button.clicked.connect(self.toggle_run)
        run_row.addWidget(save)
        run_row.addWidget(self.run_button)
        run_row.addStretch(1)
        editor.body.addLayout(run_row)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        editor.body.addWidget(self.status)
        columns.addWidget(editor, 7)

        available, reason = macros.playback_available()
        self.run_button.setEnabled(available)
        if not available:
            self.run_button.setToolTip(reason)
            set_status(self.status, reason, False)
        self._countdown_timer = QTimer(self, interval=1000, timeout=self._count)

        self.refresh_list()
        if self.macros:
            self.list.setCurrentRow(0)
        else:
            self.new_macro()

    # Library ---------------------------------------------------------------------------------

    def refresh_list(self, select: int | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for m in self.macros:
            self.list.addItem(m.name)
        self.list.blockSignals(False)
        if select is not None and 0 <= select < len(self.macros):
            self.list.setCurrentRow(select)
        self.delete_button.setEnabled(bool(self.macros))

    def new_macro(self) -> None:
        self.list.clearSelection()
        self.list.setCurrentRow(-1)
        self.name.setText("")
        self.table.setRowCount(0)
        self.add_step("key")

    def show_macro(self, row: int) -> None:
        if not 0 <= row < len(self.macros):
            return
        macro = self.macros[row]
        self.name.setText(macro.name)
        self.table.setRowCount(0)
        for step in macro.steps:
            self.add_step(step.kind, step.value)

    def delete_macro(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.macros):
            del self.macros[row]
            macros.save_macros(self.macros)
            self.refresh_list(min(row, len(self.macros) - 1))
            if not self.macros:
                self.new_macro()
            set_status(self.status, "Macro deleted.", True)

    # Steps -----------------------------------------------------------------------------------

    def add_step(self, kind: str, value: str | None = None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        action = QTableWidgetItem(STEP_LABELS[kind])
        action.setData(Qt.UserRole, kind)
        action.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        action.setForeground(qcolor(ORANGE if kind == "key" else ICE if kind == "text" else MUTED))
        self.table.setItem(row, 0, action)
        self.table.setItem(row, 1, QTableWidgetItem(STEP_DEFAULTS[kind] if value is None else value))
        self.table.selectRow(row)

    def remove_step(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def move_step(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        for column in range(2):
            a, b = self.table.takeItem(row, column), self.table.takeItem(target, column)
            self.table.setItem(row, column, b)
            self.table.setItem(target, column, a)
        self.table.selectRow(target)

    def current_macro(self) -> macros.Macro:
        steps = []
        for row in range(self.table.rowCount()):
            kind = self.table.item(row, 0).data(Qt.UserRole)
            value_item = self.table.item(row, 1)
            steps.append(macros.Step(kind, value_item.text().strip() if kind != "text" else value_item.text()))
        return macros.Macro(self.name.text().strip(), steps)

    def save_macro(self) -> bool:
        macro = self.current_macro()
        errors = macros.validate(macro)
        if errors:
            set_status(self.status, "  ".join(errors), False)
            return False
        row = self.list.currentRow()
        if 0 <= row < len(self.macros):
            self.macros[row] = macro
        else:
            self.macros.append(macro)
            row = len(self.macros) - 1
        macros.save_macros(self.macros)
        self.refresh_list(row)
        set_status(self.status, f"Saved '{macro.name}'.", True)
        return True

    # Playback --------------------------------------------------------------------------------

    def toggle_run(self) -> None:
        if self.runner is not None and self.runner.isRunning():
            self.runner.stop_requested = True
            return
        if self._countdown_timer.isActive():
            self._countdown_timer.stop()
            self.run_button.setText("Run in 3 s")
            set_status(self.status, "Cancelled.", True)
            return
        macro = self.current_macro()
        errors = macros.validate(macro)
        if errors:
            set_status(self.status, "  ".join(errors), False)
            return
        self._pending = macro
        self.countdown = 3
        self._countdown_timer.start()
        self._count(first=True)

    def _count(self, first: bool = False) -> None:
        if not first:
            self.countdown -= 1
        if self.countdown > 0:
            self.run_button.setText(f"Cancel ({self.countdown})")
            set_status(self.status, f"Switch to the target window… {self.countdown}", True)
            return
        self._countdown_timer.stop()
        self.run_button.setText("Stop")
        self.runner = MacroRunner(self._pending, self)
        self.runner.done.connect(self._finished)
        self.runner.start()
        set_status(self.status, f"Playing '{self._pending.name}'…", True)

    def _finished(self, message: str) -> None:
        self.run_button.setText("Run in 3 s")
        set_status(self.status, message, not message.startswith("Playback failed"))


# --- 04 Game Mode & HUD ---------------------------------------------------------------------------


class CompetitorToggle(QPushButton):
    """The big ULTRA FPS / COMPETITOR MODE switch."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.busy = False

    def enterEvent(self, event) -> None:
        self.update()

    def leaveEvent(self, event) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on, hover = self.isChecked(), self.underMouse()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        frame = QPainterPath()
        frame.addRoundedRect(rect, RADIUS, RADIUS)
        if on:
            fill = QLinearGradient(rect.topLeft(), rect.topRight())
            fill.setColorAt(0, qcolor(ORANGE, 0.95))
            fill.setColorAt(1, qcolor("#ff3d00", 0.85))
            p.fillPath(frame, fill)
        else:
            p.fillPath(frame, qcolor(PANEL_2))
        # Hazard stripes along the right edge.
        p.save()
        p.setClipPath(frame)
        stripes = QRectF(rect.right() - 120, rect.top(), 120, rect.height())
        for i in range(-4, 14):
            x = stripes.left() + i * 18
            band = QPainterPath(QPointF(x, rect.bottom()))
            band.lineTo(x + rect.height(), rect.top())
            band.lineTo(x + rect.height() + 9, rect.top())
            band.lineTo(x + 9, rect.bottom())
            p.fillPath(band, qcolor(BG if on else ORANGE, 0.25 if on else 0.18))
        p.restore()
        p.strokePath(frame, QPen(qcolor(ORANGE if (on or hover) else LINE), 1.5))

        ink = qcolor(BG) if on else qcolor(ORANGE)
        draw_icon(p, "bolt", QPointF(rect.left() + 46, rect.center().y()), 44, ink, 8)
        text_width = rect.width() - 86 - 20
        title_text = "ULTRA FPS / COMPETITOR MODE"
        title = QFont(self.font())
        title.setWeight(QFont.Black)
        title.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        for size in range(24, 13, -1):  # largest size that fits the button
            title.setPixelSize(size)
            if QFontMetrics(title).horizontalAdvance(title_text) <= text_width:
                break
        p.setFont(title)
        p.setPen(qcolor(BG) if on else qcolor(TEXT))
        p.drawText(QRectF(rect.left() + 86, rect.top() + 14, text_width, 34),
                   Qt.AlignLeft | Qt.AlignVCenter, title_text)
        sub = QFont(self.font())
        sub.setPixelSize(12)
        sub.setWeight(QFont.DemiBold)
        sub.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        p.setFont(sub)
        p.setPen(qcolor(BG, 0.85) if on else qcolor(MUTED))
        if self.busy:
            state = "SWITCHING…"
        elif on:
            state = "ENGAGED  //  GAMEMODE · CPU PERFORMANCE · VSYNC OFF"
        else:
            state = "STANDBY  //  CLICK TO ENGAGE"
        state = QFontMetrics(sub).elidedText(state, Qt.ElideRight, int(text_width))
        p.drawText(QRectF(rect.left() + 86, rect.top() + 50, text_width, 24),
                   Qt.AlignLeft | Qt.AlignVCenter, state)


class HudPreview(QWidget):
    """A mock game frame with the PERDIX HUD drawn where MangoHud will put it."""

    def __init__(self, settings: hud.HudSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setMinimumSize(340, 200)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        scene = QLinearGradient(rect.topLeft(), rect.bottomRight())
        scene.setColorAt(0, QColor("#202833"))
        scene.setColorAt(1, QColor("#0d0f12"))
        p.fillRect(rect, scene)
        p.setPen(QPen(qcolor(LINE), 1))
        p.drawRect(rect.adjusted(0, 0, -1, -1))

        s = self.settings
        theme = {k: "#" + v for k, v in hud.THEMES[s.color].items()}
        lines = []
        if s.metrics.get("gpu_stats") or s.metrics.get("gpu_temp"):
            lines.append(("GPU", theme["gpu_color"], "  ".join(
                v for k, v in (("gpu_stats", "97%"), ("gpu_temp", "64°C")) if s.metrics.get(k))))
        if s.metrics.get("cpu_stats") or s.metrics.get("cpu_temp"):
            lines.append(("CPU", theme["cpu_color"], "  ".join(
                v for k, v in (("cpu_stats", "41%"), ("cpu_temp", "58°C")) if s.metrics.get(k))))
        for key, label, value in (("vram", "VRAM", "5.1 GiB"), ("ram", "RAM", "9.8 GiB"),
                                  ("fps", "FPS", "347"), ("gamemode", "GAMEMODE", "ON"),
                                  ("time", "TIME", QDateTime.currentDateTime().toString("HH:mm"))):
            if s.metrics.get(key):
                lines.append((label, theme["vram_color"] if key in ("vram", "ram") else theme["text_color"], value))
        graph = s.metrics.get("frame_timing")
        if not lines and not graph:
            p.setPen(qcolor(MUTED))
            p.drawText(rect, Qt.AlignCenter, "HUD empty: no metrics selected")
            return

        scale = s.font_size / 20
        line_h = 17 * scale
        width = 170 * scale
        height = len(lines) * line_h + (34 * scale if graph else 0) + 12
        x = 10 if "left" in s.position else rect.width() - width - 10
        y = 10 if "top" in s.position else rect.height() - height - 10
        p.fillRect(QRectF(x, y, width, height), qcolor(theme["background_color"], s.background_alpha))
        font = QFont("DejaVu Sans Mono")
        font.setPixelSize(max(8, int(11 * scale)))
        font.setBold(True)
        p.setFont(font)
        for i, (label, color, value) in enumerate(lines):
            row = QRectF(x + 8, y + 6 + i * line_h, width - 16, line_h)
            p.setPen(qcolor(color))
            p.drawText(row, Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setPen(qcolor(theme["text_color"]))
            p.drawText(row, Qt.AlignRight | Qt.AlignVCenter, value)
        if graph:
            top = y + 6 + len(lines) * line_h + 4
            path = QPainterPath()
            for i in range(41):
                gx = x + 8 + i * (width - 16) / 40
                spike = 0.5 if i in (13, 29) else 0.0
                gy = top + 24 * scale * (0.6 - 0.1 * math.sin(i * 0.9) - spike)
                path.moveTo(gx, gy) if i == 0 else path.lineTo(gx, gy)
            p.strokePath(path, QPen(qcolor(theme["frametime_color"]), 1.5))


class HudPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        defaults = asdict(hud.HudSettings())
        self.settings = hud.HudSettings(**hub_settings.merged(defaults, hub_settings.load().get("hud")))
        self._worker: Worker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(PageHeader("04", "Performance", "Game Mode & HUD",
                                    "One switch for maximum frame rate, and the PERDIX HUD: MangoHud "
                                    "styled in PERDIX colours, updated the moment you click."))

        top = QHBoxLayout()
        top.setSpacing(18)
        self.competitor = CompetitorToggle()
        self.competitor.setChecked(competitor.is_active())
        self.competitor.clicked.connect(self.toggle_competitor)
        top.addWidget(self.competitor, 2)
        status_card = Card("System")
        self.gm_value = Readout("GameMode", small=True)
        self.gov_value = Readout("CPU governor", small=True)
        status_row = QHBoxLayout()
        status_row.addWidget(self.gm_value)
        status_row.addWidget(self.gov_value)
        status_card.body.addLayout(status_row)
        top.addWidget(status_card, 1)
        layout.addLayout(top)
        self.competitor_status = QLabel(objectName="Muted", wordWrap=True)
        layout.addWidget(self.competitor_status)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        layout.addLayout(columns, 1)

        theme_card = Card("PERDIX HUD theme")
        theme_card.body.addWidget(tracked_label("Colour", "Muted", 1.5))
        color_row, self.color_group = segmented([("orange", "Orange"), ("white", "White")], self.settings.color)
        theme_card.body.addLayout(color_row)
        theme_card.body.addWidget(tracked_label("Position", "Muted", 1.5))
        pos_row, self.position_group = segmented(list(hud.POSITIONS.items()), self.settings.position)
        theme_card.body.addLayout(pos_row)
        theme_card.body.addWidget(tracked_label("Show", "Muted", 1.5))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self.metric_boxes: dict[str, QCheckBox] = {}
        for i, (key, label) in enumerate(hud.METRICS.items()):
            box = QCheckBox(label, checked=bool(self.settings.metrics.get(key)))
            grid.addWidget(box, i // 2, i % 2)
            self.metric_boxes[key] = box
        theme_card.body.addLayout(grid)
        self.font_size = LabeledSlider("Text size", 12, 32, self.settings.font_size, lambda v: f"{v} px")
        theme_card.body.addWidget(self.font_size)
        columns.addWidget(theme_card, 5)

        preview_card = Card("Preview")
        self.preview = HudPreview(self.settings)
        preview_card.body.addWidget(self.preview, 1)
        launch_row = QHBoxLayout()
        launch_row.addWidget(QLabel("Steam launch options"))
        self.launch = QLineEdit(hud.LAUNCH_OPTIONS, readOnly=True)
        launch_row.addWidget(self.launch, 1)
        copy = button("Copy")
        copy.clicked.connect(self.copy_launch_options)
        launch_row.addWidget(copy)
        preview_card.body.addLayout(launch_row)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        preview_card.body.addWidget(self.status)
        columns.addWidget(preview_card, 6)

        # Theme buttons call the theme engine at once; checkboxes and the slider are batched.
        self.color_group.buttonClicked.connect(self.theme_clicked)
        self.position_group.buttonClicked.connect(self.theme_clicked)
        self._layout_timer = QTimer(self, singleShot=True, interval=300, timeout=self.write_layout)
        for box in self.metric_boxes.values():
            box.toggled.connect(self.layout_changed)
        self.font_size.valueChanged.connect(self.layout_changed)
        self.refresh_status()

    def refresh_status(self) -> None:
        self.gm_value.set(hud.gamemode_status().upper())
        self.gov_value.set((tuning.current_governor() or "n/a").upper())

    # Competitor mode ---------------------------------------------------------------------------

    def toggle_competitor(self, on: bool) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.competitor.setChecked(not on)
            return
        self.competitor.busy = True
        self.competitor.setEnabled(False)
        self._worker = Worker(competitor.activate if on else competitor.deactivate, self)
        self._worker.done.connect(self._competitor_done)
        self._worker.start()

    def _competitor_done(self, result) -> None:
        self.competitor.busy = False
        self.competitor.setEnabled(True)
        if isinstance(result, Exception):
            set_status(self.competitor_status, f"Competitor mode failed: {result}", False)
        else:
            ok = all(step_ok for _, step_ok, _ in result)
            text = "   ·   ".join(f"{step}: {message}" for step, _, message in result)
            set_status(self.competitor_status, text, ok)
        self.competitor.setChecked(competitor.is_active())
        self.refresh_status()

    # PERDIX HUD theme engine ---------------------------------------------------------------------

    def theme_clicked(self, *_args) -> None:
        self.settings.color = group_value(self.color_group)
        self.settings.position = group_value(self.position_group)
        self.preview.update()
        try:
            path = hud.apply_theme(self.settings.color, self.settings.position)
        except OSError as error:
            set_status(self.status, f"Could not update the HUD config: {error}", False)
            return
        hub_settings.save_section("hud", asdict(self.settings))
        set_status(self.status, f"PERDIX HUD: {self.settings.color}, {hud.POSITIONS[self.settings.position]} "
                                f"→ {home_path(path)}", True)

    def layout_changed(self, *_args) -> None:
        self.settings.metrics = {key: box.isChecked() for key, box in self.metric_boxes.items()}
        self.settings.font_size = self.font_size.value()
        self.preview.update()
        self._layout_timer.start()

    def write_layout(self) -> None:
        try:
            path = hud.apply_settings(self.settings)
        except OSError as error:
            set_status(self.status, f"Could not update the HUD config: {error}", False)
            return
        hub_settings.save_section("hud", asdict(self.settings))
        set_status(self.status, f"HUD updated → {home_path(path)}. Applies the next time a game starts.", True)

    def copy_launch_options(self) -> None:
        QApplication.clipboard().setText(hud.LAUNCH_OPTIONS)
        set_status(self.status, "Launch options copied.", True)


# --- 05 Network Optimization (Zero-Ping) ----------------------------------------------------------

GAME_PORT_PRESETS = {
    "Steam / Source (CS2, TF2, Dota 2)": "27015-27050",
    "Valorant / League of Legends": "7000-8000",
    "Overwatch 2": "26503-26600",
    "Apex Legends": "37000-40000",
}


class NetworkPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        saved = hub_settings.merged({"limit_mbit": 50, "ports": ",".join(GAME_PORT_PRESETS.values())},
                                    hub_settings.load().get("network"))
        self._worker: Worker | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(PageHeader("05", "Network", "Network Optimization (Zero-Ping)",
                                    "Google BBR congestion control and a traffic-control (tc) policy that "
                                    "puts game UDP first and holds background downloads back."))

        readouts_card = Card("Link status")
        row = QHBoxLayout()
        self.iface_value = Readout("Interface")
        self.tcp_value = Readout("TCP congestion control")
        self.qos_value = Readout("QoS")
        for r in (self.iface_value, self.tcp_value, self.qos_value):
            row.addWidget(r)
        readouts_card.body.addLayout(row)
        layout.addWidget(readouts_card)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        layout.addLayout(columns, 1)

        policy = Card("QoS policy")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.limit = QSpinBox(minimum=1, maximum=10000, singleStep=5, value=saved["limit_mbit"], suffix=" Mbit/s")
        self.ports = QLineEdit(saved["ports"])
        self.ports.setCursorPosition(0)
        form.addWidget(QLabel("Background download limit"), 0, 0)
        form.addWidget(self.limit, 0, 1)
        form.addWidget(QLabel("Game UDP ports"), 1, 0)
        form.addWidget(self.ports, 1, 1)
        form.setColumnStretch(1, 1)
        policy.body.addLayout(form)
        policy.body.addWidget(QLabel("Set the limit to about 80 % of your connection's download speed. "
                                     "Ports: ranges separated by commas.", objectName="Muted", wordWrap=True))
        presets = QGridLayout()
        presets.setSpacing(6)
        for i, (name, ports) in enumerate(GAME_PORT_PRESETS.items()):
            preset = button(f"+ {name}")
            preset.setToolTip(ports)
            preset.clicked.connect(lambda _c, p=ports: self.add_ports(p))
            presets.addWidget(preset, i // 2, i % 2)
        policy.body.addLayout(presets)
        policy.body.addStretch(1)
        actions = QHBoxLayout()
        self.activate = primary_button("Activate BBR and QoS")
        self.activate.setMinimumHeight(44)
        self.activate.clicked.connect(self.activate_clicked)
        self.restore = button("Restore defaults")
        self.restore.clicked.connect(self.restore_clicked)
        actions.addWidget(self.activate, 1)
        actions.addWidget(self.restore)
        policy.body.addLayout(actions)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        policy.body.addWidget(self.status)
        columns.addWidget(policy, 6)

        explain = Card("What it does")
        for title, text in (
            ("BBR", "sysctl net.ipv4.tcp_congestion_control=bbr with the fq queue: TCP paces itself "
                    "instead of filling router buffers, so pings stay low while something downloads."),
            ("Upload", "A three-band priority queue on your network card. UDP packets to the game "
                       "ports always leave first; every band uses fq_codel to keep queues short."),
            ("Download", "Game UDP from those ports passes untouched. TCP traffic (downloads, updates, "
                         "streams) is policed to the limit so it can't saturate the line."),
            ("Scope", "IPv4, this session only: everything resets on reboot or with Restore defaults."),
        ):
            explain.body.addWidget(tracked_label(title, "EyebrowOrange", 2))
            explain.body.addWidget(QLabel(text, objectName="Muted", wordWrap=True))
        explain.body.addStretch(1)
        columns.addWidget(explain, 5)
        self.refresh()

    def add_ports(self, ports: str) -> None:
        current = [p.strip() for p in self.ports.text().split(",") if p.strip()]
        if ports not in current:
            self.ports.setText(",".join(current + [ports]))

    def refresh(self) -> None:
        self.interface = tuning.default_interface()
        self.iface_value.set(self.interface or "OFFLINE")
        self.tcp_value.set((tuning.current_tcp_algorithm() or "n/a").upper())
        self.qos_value.set("ACTIVE" if tuning.qos_active(self.interface) else "OFF")

    def _run(self, steps: list[tuple[str, tuple[str, ...]]], busy: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        for b in (self.activate, self.restore):
            b.setEnabled(False)
        set_status(self.status, busy, True)

        def work():
            return [(name, *tuning.run_root(*args)) for name, args in steps]
        self._worker = Worker(work, self)
        self._worker.done.connect(self._done)
        self._worker.start()

    def _done(self, result) -> None:
        for b in (self.activate, self.restore):
            b.setEnabled(True)
        if isinstance(result, Exception):
            set_status(self.status, str(result), False)
        else:
            ok = all(step_ok for _, step_ok, _ in result)
            set_status(self.status, "   ·   ".join(f"{name}: {'done' if step_ok else message}"
                                                   for name, step_ok, message in result), ok)
        self.refresh()

    def activate_clicked(self) -> None:
        if not self.interface:
            set_status(self.status, "No network connection (no default route).", False)
            return
        ports = ",".join(p.strip() for p in self.ports.text().split(",") if p.strip())
        hub_settings.save_section("network", {"limit_mbit": self.limit.value(), "ports": ports})
        self._run([("BBR", ("tcp", "bbr")),
                   ("QoS", ("qos", "on", self.interface, str(self.limit.value()), ports))],
                  "Activating BBR and QoS…")

    def restore_clicked(self) -> None:
        steps = [("TCP", ("tcp", "cubic"))]
        if self.interface:
            steps.append(("QoS", ("qos", "off", self.interface)))
        self._run(steps, "Restoring defaults…")


# --- Main window ----------------------------------------------------------------------------------


class HubWindow(QWidget):
    MODULES = [
        ("01", "mouse", "Sens & Accel", "Acceleration · eDPI · cm/360"),
        ("02", "scope", "Tactical Scope", "Crosshair designer · overlay"),
        ("03", "macro", "Macro Studio", "Key, text and delay chains"),
        ("04", "hud", "Game Mode & HUD", "Competitor · PERDIX HUD"),
        ("05", "net", "Network", "Zero-Ping · BBR · QoS"),
    ]

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle("PERDIX Control Center")
        self.setMinimumSize(1100, 720)
        self.resize(1360, 860)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.title_bar = TitleBar(self)
        outer.addWidget(self.title_bar)

        middle = QHBoxLayout()
        middle.setSpacing(0)
        self.sidebar = Sidebar(self.MODULES)
        middle.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.sens, self.scope, self.macro = SensPage(), ScopePage(), MacroPage()
        self.hud, self.network = HudPage(), NetworkPage()
        for page in (self.sens, self.scope, self.macro, self.hud, self.network):
            holder = QWidget()
            holder_layout = QVBoxLayout(holder)
            holder_layout.setContentsMargins(32, 26, 32, 26)
            holder_layout.addWidget(page)
            self.pages.addWidget(holder)
        middle.addWidget(self.pages, 1)
        outer.addLayout(middle, 1)
        self.status_bar = StatusBar()
        outer.addWidget(self.status_bar)

        self.sidebar.group.idClicked.connect(self.pages.setCurrentIndex)
        self.sidebar.buttons[0].setChecked(True)

    def closeEvent(self, event) -> None:
        self.scope.overlay.close()
        self.scope.zoom.shutdown()
        if self.macro.runner is not None and self.macro.runner.isRunning():
            self.macro.runner.stop_requested = True
            self.macro.runner.wait(2000)
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(BG))
        # Faint dot grid behind the content.
        p.setPen(QPen(qcolor(ICE, 0.05), 1))
        for x in range(272, self.width(), 24):
            for y in range(48, self.height() - 30, 24):
                p.drawPoint(x, y)
        # Frame with orange corner brackets.
        p.setPen(QPen(qcolor(LINE), 1))
        p.drawRect(QRectF(self.rect()).adjusted(0, 0, -1, -1))
        w, h, n = self.width() - 1, self.height() - 1, 18
        for x0, y0, dx, dy in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)):
            p.fillRect(QRectF(min(x0, x0 + dx * n), y0 - (0 if dy > 0 else 1), n, 2), qcolor(ORANGE))
            p.fillRect(QRectF(x0 - (0 if dx > 0 else 1), min(y0, y0 + dy * n), 2, n), qcolor(ORANGE))


def apply_saved_mouse_settings() -> int:
    """Re-apply the saved acceleration settings (xinput settings reset on every login)."""
    saved = hub_settings.load().get("mouse")
    if not isinstance(saved, dict) or not saved.get("device"):
        return 0
    settings = hub_settings.merged({"device": "", "curve": "off", "amount": 0.35}, saved)
    if settings["curve"] not in mouse.CURVES:
        return 0
    xinput = mouse.XInput()
    for pointer in (p for p in xinput.pointers() if p.name == settings["device"]):
        ok, message = xinput.apply_curve(pointer.id, settings["curve"], settings["amount"])
        print(f"{pointer.name}: {message}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PERDIX Control Center")
    parser.add_argument("--apply-saved", action="store_true",
                        help="re-apply saved mouse settings and exit (for session start)")
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if args.apply_saved:
        return apply_saved_mouse_settings()

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("PERDIX Control Center")
    app.setStyleSheet(STYLESHEET)
    window = HubWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
