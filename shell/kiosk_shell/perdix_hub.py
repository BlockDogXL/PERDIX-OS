"""PERDIX Control Center: the esports tuning hub of PERDIX OS.

A frameless, custom-chromed PyQt5 window (dark graphite with neon-orange and ice-blue
accents, 4 px corners). A grouped, scrolling menu on the left (NAV below; module numbers follow
its order) switches the module shown on the right:

Gaming
  Sens & Accel         Raw Accel-style curves (linear / natural / classic) on libinput, with a
                       graph; eDPI / cm-per-360 calculator and game converter
  Tactical Scope       crosshair designer and overlay; zoom key (hold to magnify the centre)
  Macro Studio         key / text / delay macros, saved as JSON, played through uinput
  Game Mode & HUD      ULTRA FPS / COMPETITOR MODE switch; PERDIX HUD theme engine for MangoHud
  Vision               PERDIX Vision: brightness, contrast, vibrance, sharpening, shadow boost
                       (lift/gamma/gain) and .cube 3D LUTs for every game, applied live by gamescope
Network & Sound
  Zero-Ping            BBR congestion control and tc QoS (game UDP first)
  Network Settings     Wi-Fi and Ethernet through NetworkManager (nmcli): background scan, join
                       with a password; wired connections come up on their own
  Audio                PipeWire output/microphone device and volume (wpctl); one-click EasyEffects
                       effects: volume boost, bass boost, microphone noise suppression
  Bluetooth            scan, pair (just-works agent), trust and connect headphones, speakers, game
                       controllers, mice and keyboards (bluetoothctl); Blueman for the rest
System
  Task Manager         CPU, RAM and GPU use with history, running programs, End task / Kill;
                       hung programs (no answer to _NET_WM_PING) are flagged and killed in one click
  Diagnostics          PERDIX System Diagnostics: GPU, Vulkan/OpenGL, DirectX through Proton (DXVK,
                       VKD3D-Proton), RAM, BIOS, displays, disks; saved or copied as a text report
  Region & Language    time zone and internet time (timedatectl), keyboard layout with an optional
                       second one on Alt+Shift (setxkbmap now, localectl for later boots), language
  Devices              motherboard and BIOS, PCI parts (graphics, sound, network, storage) with the
                       driver in use (lspci -k), keyboards / mice / controllers, USB devices (lsusb)
Maintenance
  Kılavuz              PERDIX Sentinel, in Turkish: open ports and weak file permissions, and
                       "Açıkları Kapat" (close vulnerabilities) for the safe permission fixes

Root-level changes (CPU governor, sysctl, tc, hardening) go through
`pkexec /usr/local/bin/perdix-tune`.

User settings live in ~/.config/perdix-os/ (settings.json, macros.json, theme.json for the
orange / ice-blue accents); the HUD config is written to ~/.config/MangoHud/MangoHud.conf.
System settings (game mode, network, HUD, GPU profile) are recorded by perdix-tune in
/etc/perdix/perdix.conf and drivers.conf and re-applied at boot; see kiosk_shell/paths.py.

Run:   python -m kiosk_shell.perdix_hub          (from shell/)
       python kiosk_shell/perdix_hub.py
       python -m kiosk_shell.perdix_hub --maximized     (as the desktop's Control Center tile does)
       python -m kiosk_shell.perdix_hub --apply-saved   (re-apply mouse settings and sound effects,
                                                          no window; run from .xinitrc at session start)
"""

from __future__ import annotations

import argparse
import configparser
import math
import os
import sys
from dataclasses import asdict, dataclass

if __package__ in (None, ""):  # run as a plain script: make the kiosk_shell package importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QDateTime, QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import (QBitmap, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiosk_shell import __version__, paths
from kiosk_shell.services import (audio, bluetooth, competitor, devices, diagnostics, hub_settings, hud, keywatch,
                                  kilavuz,
                                  macros, magnifier, mouse, network, perflog, region, taskman, theme, tuning,
                                  vision)

# --- Look -----------------------------------------------------------------------------------------

BG = "#0a0b0d"
PANEL = "#111317"
PANEL_2 = "#171a1f"
FIELD = "#0d0f12"
LINE = "#262a31"
_THEME = theme.load()  # ~/.config/perdix-os/theme.json
ORANGE = _THEME["orange"]
ORANGE_HOT = QColor(ORANGE).lighter(125).name()
ICE = _THEME["ice"]


def _rgba(hex_color: str, alpha: int) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"
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
QPushButton#Segment:checked {{ background: {_rgba(ORANGE, 38)}; border-color: {ORANGE}; color: {ORANGE}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {FIELD}; border: 1px solid {LINE}; border-radius: {RADIUS}px;
    padding: 6px 10px; selection-background-color: {ORANGE}; selection-color: {BG};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ORANGE}; }}
QLineEdit[readOnly="true"] {{ color: {ICE}; font-family: {MONO_STACK}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {PANEL_2}; border: 1px solid {LINE}; selection-background-color: {_rgba(ORANGE, 60)};
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

QListWidget, QTableWidget, QTreeWidget {{
    background: {FIELD}; border: 1px solid {LINE}; border-radius: {RADIUS}px; outline: none;
    gridline-color: {LINE}; selection-background-color: {_rgba(ICE, 40)}; selection-color: {ICE};
}}
QHeaderView {{ background: {PANEL_2}; border: none; }}
QHeaderView::section:vertical {{
    background: {PANEL_2}; color: {MUTED}; border: none; border-right: 1px solid {LINE}; padding: 0 6px;
}}
QListWidget::item {{ padding: 9px 10px; border-left: 2px solid transparent; }}
QListWidget::item:hover {{ background: {PANEL_2}; }}
QListWidget::item:selected {{
    background: {_rgba(ORANGE, 30)}; color: {ORANGE}; border-left: 2px solid {ORANGE};
}}
QTableWidget::item {{ padding: 4px 8px; }}
QTreeWidget::item {{ padding: 4px 4px; }}
QTreeWidget::item:selected {{ background: {_rgba(ICE, 30)}; color: {ICE}; }}
QTableWidget::item:selected {{ background: {_rgba(ICE, 30)}; color: {ICE}; }}
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
    def __init__(self, index: str, area: str, title: str, text: str, parent=None, module_word: str = "Module") -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(4)
        layout.addWidget(tracked_label(f"{module_word} {index}  //  {area}", "EyebrowOrange"))
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


# --- Modules ---------------------------------------------------------------------------------------

# The menu, in order: (key, icon, title, subtitle, group). A module's number is its position, so
# adding a module renumbers the ones after it automatically. The key is also the HubWindow
# attribute that holds the page.
NAV = [
    ("sens", "mouse", "Sens & Accel", "Acceleration · eDPI · cm/360", "Gaming"),
    ("scope", "scope", "Tactical Scope", "Crosshair designer · overlay", "Gaming"),
    ("macro", "macro", "Macro Studio", "Key, text and delay chains", "Gaming"),
    ("hud", "hud", "Game Mode & HUD", "Competitor · PERDIX HUD", "Gaming"),
    ("vision", "vision", "Vision", "Colour · sharpening · LUT", "Gaming"),
    ("network", "net", "Zero-Ping", "BBR · QoS · game priority", "Network & Sound"),
    ("wifi", "wifi", "Network Settings", "Wi-Fi · Ethernet", "Network & Sound"),
    ("audio", "audio", "Audio", "Volume · EasyEffects", "Network & Sound"),
    ("bluetooth", "bluetooth", "Bluetooth", "Headsets · controllers", "Network & Sound"),
    ("taskman", "tasks", "Task Manager", "CPU · RAM · GPU · processes", "System"),
    ("devices", "chip", "Devices", "Hardware · drivers · USB", "System"),
    ("diagnostics", "pulse", "Diagnostics", "GPU · Vulkan · DirectX · BIOS", "System"),
    ("region", "globe", "Region & Language", "Time zone · keyboard · language", "System"),
    ("security", "shield", "Kılavuz", "Güvenlik · port · izin", "Maintenance"),
]


def module_number(key: str) -> str:
    """'05' for the fifth entry of NAV."""
    return f"{[entry[0] for entry in NAV].index(key) + 1:02d}"


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
    elif kind == "wifi":
        for r in (16, 32, 48):
            p.arcMoveTo(QRectF(-r, 26 - r, 2 * r, 2 * r), 50)
            p.arcTo(QRectF(-r, 26 - r, 2 * r, 2 * r), 50, 80)
        p.addEllipse(QPointF(0, 26), 5, 5)
        p.moveTo(-44, 40); p.lineTo(44, 40)
    elif kind == "bluetooth":  # the Bluetooth rune
        p.moveTo(-22, -22); p.lineTo(22, 20); p.lineTo(0, 42); p.lineTo(0, -42); p.lineTo(22, -20)
        p.lineTo(-22, 22)
    elif kind == "globe":  # a globe with meridians
        p.addEllipse(QPointF(0, 0), 42, 42)
        p.addEllipse(QPointF(0, 0), 18, 42)
        p.moveTo(-42, 0); p.lineTo(42, 0)
        p.moveTo(-36, -22); p.lineTo(36, -22); p.moveTo(-36, 22); p.lineTo(36, 22)
    elif kind == "pulse":  # a monitor with a heartbeat line
        p.addRoundedRect(QRectF(-44, -32, 88, 58), 4, 4)
        p.moveTo(-14, 40); p.lineTo(14, 40); p.moveTo(0, 26); p.lineTo(0, 40)
        p.moveTo(-34, 0); p.lineTo(-14, 0); p.lineTo(-6, -18); p.lineTo(6, 16); p.lineTo(14, 0); p.lineTo(34, 0)
    elif kind == "chip":  # a chip with pins
        p.addRect(QRectF(-26, -26, 52, 52))
        p.addRect(QRectF(-12, -12, 24, 24))
        for i in (-16, 0, 16):
            for x1, y1, x2, y2 in ((i, -26, i, -40), (i, 26, i, 40), (-26, i, -40, i), (26, i, 40, i)):
                p.moveTo(x1, y1); p.lineTo(x2, y2)
    elif kind == "tasks":  # activity bars under a pulse line
        for x, h in ((-30, 20), (-12, 40), (6, 28), (24, 50)):
            p.addRect(QRectF(x, 44 - h, 12, h))
        p.moveTo(-46, -20); p.lineTo(-22, -20); p.lineTo(-12, -42); p.lineTo(2, -4); p.lineTo(12, -24)
        p.lineTo(46, -24)
    elif kind == "vision":  # eye with a crosshair pupil
        p.moveTo(-46, 0); p.cubicTo(-22, -34, 22, -34, 46, 0); p.cubicTo(22, 34, -22, 34, -46, 0)
        p.addEllipse(QPointF(0, 0), 14, 14)
        p.moveTo(0, -24); p.lineTo(0, -16); p.moveTo(0, 16); p.lineTo(0, 24)
    elif kind == "audio":  # speaker with sound waves
        p.moveTo(-40, -14); p.lineTo(-22, -14); p.lineTo(0, -34); p.lineTo(0, 34); p.lineTo(-22, 14)
        p.lineTo(-40, 14); p.closeSubpath()
        for r in (16, 32):
            p.arcMoveTo(QRectF(4 - r, -r, 2 * r, 2 * r), -50)
            p.arcTo(QRectF(4 - r, -r, 2 * r, 2 * r), -50, 100)
    elif kind == "shield":
        p.moveTo(0, -44); p.lineTo(36, -30); p.lineTo(36, 0)
        p.cubicTo(36, 24, 18, 38, 0, 46); p.cubicTo(-18, 38, -36, 24, -36, 0)
        p.lineTo(-36, -30); p.closeSubpath()
        p.moveTo(-16, 0); p.lineTo(-4, 12); p.lineTo(18, -12)
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
        self.setFixedHeight(56)
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
        top = (self.height() - 38) / 2  # title and subtitle centred as a block
        p.drawText(QRectF(88, top, self.width() - 96, 20), Qt.AlignLeft | Qt.AlignVCenter, self.title.upper())
        sub = QFont(self.font())
        sub.setPixelSize(12)
        p.setFont(sub)
        p.setPen(qcolor(MUTED))
        p.drawText(QRectF(88, top + 20, self.width() - 96, 18), Qt.AlignLeft | Qt.AlignVCenter, self.subtitle)


class Sidebar(QWidget):
    """The module menu: group headings and entries, scrolling when the window is too short."""

    def __init__(self, entries: list[tuple[str, str, str, str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(272)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 22, 0, 18)
        layout.setSpacing(4)
        heading = tracked_label("Navigation", "Muted", 3)
        heading.setContentsMargins(18, 0, 0, 6)
        layout.addWidget(heading)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = []
        self.headings = []
        last_group = None
        for i, (_key, icon, title, subtitle, group) in enumerate(entries):
            if group != last_group:
                label = tracked_label(group, "EyebrowOrange", 2)
                label.setContentsMargins(18, 10 if last_group else 2, 0, 4)
                inner_layout.addWidget(label)
                self.headings.append(label)
                last_group = group
            b = NavButton(f"{i + 1:02d}", icon, title, subtitle)
            self.group.addButton(b, i)
            self.buttons.append(b)
            inner_layout.addWidget(b)
        inner_layout.addStretch(1)
        self.scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.scroll.setWidget(inner)
        layout.addWidget(self.scroll, 1)
        layout.addSpacing(30)  # room for the hazard stripes
        footer = tracked_label("PERDIX OS  //  Esports", "Muted", 2)
        footer.setContentsMargins(18, 0, 0, 0)
        layout.addWidget(footer)
        self.group.idClicked.connect(lambda i: self.scroll.ensureWidgetVisible(self.buttons[i]))

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
        layout.addWidget(PageHeader(module_number("sens"), "Input", "Sens & Accel",
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
        layout.addWidget(PageHeader(module_number("scope"), "Optics", "Tactical Scope",
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
        layout.addWidget(PageHeader(module_number("macro"), "Automation", "Macro Studio",
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
        layout.addWidget(PageHeader(module_number("hud"), "Performance", "Game Mode & HUD",
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
        drivers = configparser.ConfigParser()
        drivers.read(paths.DRIVERS_CONF, encoding="utf-8")
        gpu_row, self.gpu_group = segmented([("auto", "GPU auto"), ("performance", "Max"), ("powersave", "Quiet")],
                                            drivers.get("gpu", "profile", fallback="auto"))
        status_card.body.addLayout(gpu_row)
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
        self.record_perf = QCheckBox("Record FPS and frame times to /var/log/perdix/perf.log",
                                     checked=self.settings.record_perf)
        theme_card.body.addWidget(self.record_perf)
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
        preview_card.body.addWidget(tracked_label("Last sessions  (perf.log)", "Muted", 1.5))
        self.perf = QLabel(objectName="Value", wordWrap=True)
        preview_card.body.addWidget(self.perf)
        columns.addWidget(preview_card, 6)
        self._record_worker: Worker | None = None
        self._gpu_worker: Worker | None = None

        # Theme buttons call the theme engine at once; checkboxes and the slider are batched.
        self.color_group.buttonClicked.connect(self.theme_clicked)
        self.position_group.buttonClicked.connect(self.theme_clicked)
        self._layout_timer = QTimer(self, singleShot=True, interval=300, timeout=self.write_layout)
        for box in self.metric_boxes.values():
            box.toggled.connect(self.layout_changed)
        self.font_size.valueChanged.connect(self.layout_changed)
        self.record_perf.toggled.connect(self.layout_changed)
        self.gpu_group.buttonClicked.connect(self.gpu_clicked)
        self.refresh_status()

    def refresh_status(self) -> None:
        self.gm_value.set(hud.gamemode_status().upper())
        self.gov_value.set((tuning.current_governor() or "n/a").upper())
        try:
            perflog.collect()
        except OSError:
            pass
        entries = perflog.last_entries(2)
        self.perf.setText("\n".join(" ".join(e.split()[1:]) for e in entries) or "No sessions recorded yet.")

    def gpu_clicked(self, *_args) -> None:
        profile = group_value(self.gpu_group)
        if self._gpu_worker is not None and self._gpu_worker.isRunning():
            return
        set_status(self.competitor_status, f"GPU profile: {profile}…", True)
        self._gpu_worker = Worker(lambda: tuning.run_root("gpu", profile), self)
        self._gpu_worker.done.connect(lambda result: set_status(
            self.competitor_status,
            f"GPU profile: {profile} (saved in /etc/perdix/drivers.conf)" if result == (True, "OK")
            else f"GPU profile not changed: {result[1] if isinstance(result, tuple) else result}",
            result == (True, "OK")))
        self._gpu_worker.start()

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
        # Also record the theme system-wide in /etc/perdix/perdix.conf (in the background).
        if self._record_worker is None or not self._record_worker.isRunning():
            color, position = self.settings.color, self.settings.position
            self._record_worker = Worker(lambda: tuning.run_root("hud", color, position), self)
            self._record_worker.start()

    def layout_changed(self, *_args) -> None:
        self.settings.metrics = {key: box.isChecked() for key, box in self.metric_boxes.items()}
        self.settings.font_size = self.font_size.value()
        self.settings.record_perf = self.record_perf.isChecked()
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
        layout.addWidget(PageHeader(module_number("network"), "Network", "Network Optimization (Zero-Ping)",
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
            ("Scope", "IPv4. Saved in /etc/perdix/perdix.conf and re-applied at every boot by "
                      "perdix-restore.service, until you press Restore defaults."),
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


# --- 06 Network Settings (Wi-Fi & Ethernet) ------------------------------------------------------

CONNECTIVITY = {"full": "ONLINE", "limited": "LIMITED", "portal": "LOGIN PAGE", "none": "OFFLINE"}


def signal_bars(signal: int) -> str:
    bars = max(1, min(4, (signal + 24) // 25))
    return "▂▄▆█"[:bars] + "·" * (4 - bars) + f"  {signal}%"


class WifiPage(QWidget):
    """Wi-Fi and Ethernet through NetworkManager. The list refreshes every few seconds while the
    page is open; picking a network and entering its password (Enter) joins it."""

    REFRESH_MS = 10000
    COLUMNS = ("Network", "Signal", "Security", "Status")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self.state = network.NetworkState(available=False, problem="Not loaded yet.")
        self.networks: list[network.WifiNetwork] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(PageHeader(module_number("wifi"), "Network", "Network & Wi-Fi Settings",
                                    "Wi-Fi and Ethernet through NetworkManager. A network cable works the moment "
                                    "it is plugged in; for Wi-Fi, pick a network and enter its password."))

        status_card = Card("Connection")
        row = QHBoxLayout()
        self.internet_value = Readout("Internet", small=True)
        self.ethernet_value = Readout("Ethernet", small=True)
        self.wifi_value = Readout("Wi-Fi", small=True)
        self.address_value = Readout("IP address", small=True)
        for r in (self.internet_value, self.ethernet_value, self.wifi_value, self.address_value):
            row.addWidget(r)
        row.addStretch(1)
        self.wifi_switch = QCheckBox("Wi-Fi on", cursor=Qt.PointingHandCursor)
        self.wifi_switch.clicked.connect(self.toggle_wifi)
        row.addWidget(self.wifi_switch)
        status_card.body.addLayout(row)
        layout.addWidget(status_card)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        layout.addLayout(columns, 1)

        wifi_card = Card("Wi-Fi networks")
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([c.upper() for c in self.COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self.connect_clicked())
        wifi_card.body.addWidget(self.table, 1)

        join = QHBoxLayout()
        self.password = QLineEdit(echoMode=QLineEdit.Password, placeholderText="Select a network")
        self.password.returnPressed.connect(self.connect_clicked)  # Enter joins: no extra click
        self.show_password = QCheckBox("Show", cursor=Qt.PointingHandCursor)
        self.show_password.toggled.connect(
            lambda on: self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.connect_button = primary_button("Connect")
        self.connect_button.setMinimumHeight(40)
        self.connect_button.clicked.connect(self.connect_clicked)
        join.addWidget(self.password, 1)
        join.addWidget(self.show_password)
        join.addWidget(self.connect_button)
        wifi_card.body.addLayout(join)
        actions = QHBoxLayout()
        self.scan_button = button("Scan now")
        self.scan_button.clicked.connect(lambda: self.refresh(rescan=True))
        self.disconnect_button = button("Disconnect")
        self.disconnect_button.clicked.connect(self.disconnect_clicked)
        self.forget_button = button("Forget network")
        self.forget_button.clicked.connect(self.forget_clicked)
        for b in (self.scan_button, self.disconnect_button, self.forget_button):
            actions.addWidget(b)
        actions.addStretch(1)
        wifi_card.body.addLayout(actions)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        wifi_card.body.addWidget(self.status)
        columns.addWidget(wifi_card, 6)

        wired = Card("Ethernet")
        self.wired_list = QLabel("—", wordWrap=True)
        self.wired_list.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        wired.body.addWidget(self.wired_list)
        for title, text in (
            ("Plug and play", "Plug in a network cable and PERDIX connects on its own: NetworkManager asks "
                              "the router for an address (DHCP). There is nothing to set up."),
            ("Wi-Fi", "The list refreshes every few seconds while this page is open. Select a network, "
                      "type its password and press Enter. The password is kept for the next boot, "
                      "readable only by the system."),
            ("Both", "With a cable and Wi-Fi at once, the cable is preferred: it has lower, steadier "
                     "ping for online games."),
        ):
            wired.body.addWidget(tracked_label(title, "EyebrowOrange", 2))
            wired.body.addWidget(QLabel(text, objectName="Muted", wordWrap=True))
        wired.body.addStretch(1)
        columns.addWidget(wired, 4)

        self._timer = QTimer(self, interval=self.REFRESH_MS, timeout=self.refresh)
        self.selection_changed()

    # --- background refresh ---

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh(rescan=not self.state.available)
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _run(self, func, done) -> None:
        self._worker = Worker(func, self)
        self._worker.done.connect(done)
        self._worker.start()

    def refresh(self, rescan: bool = False) -> None:
        if self.busy():
            return  # a scan or a connection attempt is still running
        if rescan:
            set_status(self.status, "Scanning for Wi-Fi networks…", True)
        self._run(lambda: network.state(rescan), self.show_state)

    def show_state(self, state) -> None:
        if isinstance(state, Exception):
            state = network.NetworkState(available=False, problem=str(state))
        self.state = state
        if self.status.text() == "Scanning for Wi-Fi networks…":
            self.status.setText("")
        if not state.available:
            for r in (self.internet_value, self.ethernet_value, self.wifi_value, self.address_value):
                r.set("—")
            self.wired_list.setText("—")
            self.fill_table([])
            set_status(self.status, state.problem, False)
            self.update_buttons()
            return
        self.internet_value.set(CONNECTIVITY.get(state.connectivity, "—"))
        wired = state.of_type("ethernet")
        wired_up = [d for d in wired if d.connected]
        self.ethernet_value.set("CONNECTED" if wired_up else "NO CABLE" if wired else "NONE")
        wifi = state.wifi_device
        if wifi is None:
            self.wifi_value.set("NO ADAPTER")
        elif not state.wifi_enabled:
            self.wifi_value.set("OFF")
        else:
            self.wifi_value.set(wifi.connection.upper() if wifi.connected else "NOT CONNECTED")
        connected = wired_up + ([wifi] if wifi is not None and wifi.connected else [])
        addresses = [state.addresses[d.name] for d in connected if state.addresses.get(d.name)]
        self.address_value.set(addresses[0] if addresses else "—")  # the cable's address first
        self.wifi_switch.setEnabled(wifi is not None)
        self.wifi_switch.setChecked(state.wifi_enabled and wifi is not None)
        lines = []
        for d in wired:
            if d.connected:
                address = state.addresses.get(d.name)
                lines.append(f"{d.name}: connected{f' · {address}' if address else ''}")
            elif d.state == "unavailable":
                lines.append(f"{d.name}: no cable plugged in")
            else:
                lines.append(f"{d.name}: {d.state}")
        self.wired_list.setText("\n".join(lines) or "No wired network port found.")
        self.fill_table(state.networks)
        if wifi is None:
            set_status(self.status, "No Wi-Fi adapter found. Use a network cable, or plug in a USB Wi-Fi adapter.",
                       False)
        elif not state.wifi_enabled:
            set_status(self.status, "Wi-Fi is off. Turn it on to see networks.", True)
        self.update_buttons()

    def fill_table(self, networks: list[network.WifiNetwork]) -> None:
        selected = self.selected()
        keep = selected.ssid if selected else None
        self.networks = networks
        self.table.blockSignals(True)
        self.table.setRowCount(len(networks))
        for row, net in enumerate(networks):
            status = "Connected" if net.in_use else "Saved" if net.saved else ""
            cells = (net.ssid, signal_bars(net.signal), net.security or "Open", status)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 3 and net.in_use:
                    item.setForeground(qcolor(ICE))
                if column == 2 and net.open:
                    item.setForeground(qcolor(ORANGE))
                self.table.setItem(row, column, item)
            if net.ssid == keep:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self.selection_changed()

    # --- selection and actions ---

    def selected(self) -> network.WifiNetwork | None:
        rows = self.table.selectionModel().selectedRows()
        return self.networks[rows[0].row()] if rows and rows[0].row() < len(self.networks) else None

    def selection_changed(self) -> None:
        net = self.selected()
        if net is None:
            hint, enabled = "Select a network", False
        elif net.in_use:
            hint, enabled = "Connected", False
        elif net.open:
            hint, enabled = "Open network: no password needed", False
        elif net.enterprise:
            hint, enabled = "Enterprise (802.1X) networks are not supported here", False
        elif net.saved:
            hint, enabled = "Saved: leave empty to use the saved password", True
        else:
            hint, enabled = f"Password for {net.ssid}", True
        self.password.setPlaceholderText(hint)
        self.password.setEnabled(enabled)
        if not enabled:
            self.password.clear()
        self.update_buttons()

    def update_buttons(self) -> None:
        net, idle = self.selected(), not self.busy()
        wifi = self.state.wifi_device if self.state.available else None
        self.connect_button.setEnabled(idle and net is not None and not net.in_use and not net.enterprise)
        self.disconnect_button.setEnabled(idle and wifi is not None and wifi.connected)
        self.forget_button.setEnabled(idle and net is not None and net.saved)
        self.scan_button.setEnabled(idle and wifi is not None and self.state.wifi_enabled)

    def _action(self, func, busy_text: str) -> None:
        if self.busy():
            return
        set_status(self.status, busy_text, True)
        self._run(func, self._action_done)
        self.update_buttons()

    def _action_done(self, result) -> None:
        if isinstance(result, Exception):
            set_status(self.status, str(result), False)
        else:
            set_status(self.status, result, True)
        self.password.clear()
        self.refresh()

    def connect_clicked(self) -> None:
        net = self.selected()
        if net is None or net.in_use or net.enterprise or self.busy():
            return
        password = self.password.text() if self.password.isEnabled() else ""
        if not password and not net.open and not net.saved:
            set_status(self.status, f"Enter the password for {net.ssid}.", False)
            self.password.setFocus()
            return
        device = self.state.wifi_device.name if self.state.wifi_device else None
        self._action(lambda: network.connect_wifi(net.ssid, password or None, device, net.security),
                     f"Connecting to {net.ssid}…")

    def disconnect_clicked(self) -> None:
        wifi = self.state.wifi_device
        if wifi is not None and wifi.connected:
            name = wifi.connection
            self._action(lambda: (network.disconnect(wifi.name), f"Disconnected from {name}.")[1], "Disconnecting…")

    def forget_clicked(self) -> None:
        net = self.selected()
        if net is not None and net.saved:
            self._action(lambda: (network.forget(net.ssid), f"Forgot {net.ssid}; its password was removed.")[1],
                         f"Forgetting {net.ssid}…")

    def toggle_wifi(self, on: bool) -> None:
        self._action(lambda: (network.set_wifi_enabled(on), f"Wi-Fi turned {'on' if on else 'off'}.")[1],
                     "Turning Wi-Fi on…" if on else "Turning Wi-Fi off…")

    def shutdown(self) -> None:
        self._timer.stop()
        if self.busy():
            self._worker.wait()


# --- 08 Audio & Effects ---------------------------------------------------------------------------

LEVELS = [("off", "Off"), ("low", "Low"), ("medium", "Medium"), ("high", "High")]


class AudioPage(QWidget):
    """PipeWire devices and volume, and EasyEffects as three one-click effects."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self.effects = audio.Effects.from_dict(hub_settings.load().get("audio"))
        self.outputs: list[audio.Device] = []
        self.inputs: list[audio.Device] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(PageHeader(module_number("audio"), "Audio", "Audio & Effects",
                                    "PipeWire sound, plus one-click EasyEffects: louder volume, deeper bass and "
                                    "a microphone without background noise."))

        devices = QHBoxLayout()
        devices.setSpacing(18)
        self.output_device, self.output_volume, self.output_mute, output_card = self._device_card("Output")
        self.input_device, self.input_volume, self.input_mute, input_card = self._device_card("Microphone")
        devices.addWidget(output_card)
        devices.addWidget(input_card)
        layout.addLayout(devices)
        self.output_device.activated.connect(lambda i: self._set_default(self.outputs, i))
        self.input_device.activated.connect(lambda i: self._set_default(self.inputs, i))
        self.output_mute.toggled.connect(lambda on: self._call(audio.set_muted, on, audio.SINK))
        self.input_mute.toggled.connect(lambda on: self._call(audio.set_muted, on, audio.SOURCE))
        # Volume changes go to PipeWire a moment after the slider stops, not on every pixel.
        self._volume_timer = QTimer(self, singleShot=True, interval=120, timeout=self._send_volumes)
        self.output_volume.valueChanged.connect(lambda _v: self._volume_timer.start())
        self.input_volume.valueChanged.connect(lambda _v: self._volume_timer.start())

        effects_card = Card("Effects  //  EasyEffects")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.boost_row, self.boost_group = segmented(LEVELS, self.effects.volume_boost)
        self.bass_row, self.bass_group = segmented(LEVELS, self.effects.bass_boost)
        self.noise_row, self.noise_group = segmented([("off", "Off"), ("on", "On")],
                                                     "on" if self.effects.noise_suppression else "off")
        for row, (title, text, segments) in enumerate((
            ("Volume boost", "Up to +9 dB louder than 100 %; a limiter keeps it from distorting.", self.boost_row),
            ("Bass boost", "Fuller low end through the Calf bass enhancer.", self.bass_row),
            ("Mic noise suppression", "RNNoise filters fans, keys and traffic out of your voice.",
             self.noise_row),
        )):
            label = QVBoxLayout()
            label.setSpacing(2)
            label.addWidget(tracked_label(title, "EyebrowOrange", 2))
            label.addWidget(QLabel(text, objectName="Muted", wordWrap=True))
            grid.addLayout(label, row, 0)
            grid.addLayout(segments, row, 1)
        grid.setColumnStretch(0, 1)
        effects_card.body.addLayout(grid)
        for group in (self.boost_group, self.bass_group, self.noise_group):
            group.buttonClicked.connect(lambda _b: self.apply_effects())
        bottom = QHBoxLayout()
        self.effects_status = QLabel(objectName="Muted", wordWrap=True)
        bottom.addWidget(self.effects_status, 1)
        self.open_button = button("Open EasyEffects (equalizer)")
        self.open_button.clicked.connect(self.open_easyeffects)
        bottom.addWidget(self.open_button)
        effects_card.body.addLayout(bottom)
        layout.addWidget(effects_card)
        layout.addStretch(1)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        layout.addWidget(self.status)
        self.show_effects_state()

    def _device_card(self, title: str):
        card = Card(title)
        device = QComboBox()
        volume = LabeledSlider("Volume", 0, 100, 50, lambda v: f"{v} %")
        mute = QCheckBox("Mute", cursor=Qt.PointingHandCursor)
        card.body.addWidget(device)
        card.body.addWidget(volume)
        card.body.addWidget(mute)
        return device, volume, mute, card

    # --- PipeWire ---

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        """Devices and volumes from PipeWire (quick: a few ms)."""
        try:
            self.outputs = audio.devices("Audio/Sink")
            self.inputs = audio.devices("Audio/Source")
            out, mic = audio.get_volume(audio.SINK), audio.get_volume(audio.SOURCE) if self.inputs else None
        except audio.AudioError as error:
            set_status(self.status, str(error), False)
            for widget in (self.output_device, self.output_volume, self.output_mute,
                           self.input_device, self.input_volume, self.input_mute):
                widget.setEnabled(False)
            return
        for combo, items, empty in ((self.output_device, self.outputs, "No speakers or headphones found"),
                                    (self.input_device, self.inputs, "No microphone found")):
            combo.blockSignals(True)
            combo.clear()
            for d in items:
                combo.addItem(d.description)
            if not items:
                combo.addItem(empty)
            combo.setEnabled(bool(items))
            combo.blockSignals(False)
        for slider, mute, volume in ((self.output_volume, self.output_mute, out), (self.input_volume, self.input_mute, mic)):
            slider.setEnabled(volume is not None)
            mute.setEnabled(volume is not None)
            if volume is not None:
                slider.slider.blockSignals(True)
                slider.slider.setValue(round(volume.level * 100))
                slider.readout.setText(f"{round(volume.level * 100)} %")
                slider.slider.blockSignals(False)
                mute.blockSignals(True)
                mute.setChecked(volume.muted)
                mute.blockSignals(False)
        self.status.setText("")

    def _set_default(self, devices: list[audio.Device], index: int) -> None:
        if 0 <= index < len(devices):
            self._call(audio.set_default, devices[index])
            self.refresh()

    def _send_volumes(self) -> None:
        if self.output_volume.isEnabled():
            self._call(audio.set_volume, self.output_volume.value() / 100, audio.SINK)
        if self.input_volume.isEnabled():
            self._call(audio.set_volume, self.input_volume.value() / 100, audio.SOURCE)

    def _call(self, func, *args) -> None:
        try:
            func(*args)
        except audio.AudioError as error:
            set_status(self.status, str(error), False)

    # --- EasyEffects ---

    def current_effects(self) -> audio.Effects:
        return audio.Effects(group_value(self.boost_group) or "off", group_value(self.bass_group) or "off",
                             group_value(self.noise_group) == "on")

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def apply_effects(self) -> None:
        if self.busy():
            return  # the latest choice is applied when this run ends
        self.effects = self.current_effects()
        hub_settings.save_section("audio", self.effects.to_dict())
        set_status(self.effects_status, "Applying…" if not self.effects.any else
                   "Starting EasyEffects…" if not audio.easyeffects_running() else "Applying…", True)
        effects = self.effects
        self._worker = Worker(lambda: audio.apply_effects(effects), self)
        self._worker.done.connect(self._effects_done)
        self._worker.start()

    def _effects_done(self, result) -> None:
        if isinstance(result, Exception):
            set_status(self.effects_status, str(result), False)
        else:
            set_status(self.effects_status, result, True)
        if self.current_effects() != self.effects:  # changed while applying
            self.apply_effects()

    def show_effects_state(self) -> None:
        if not audio.easyeffects_installed():
            set_status(self.effects_status, "EasyEffects is not installed.", False)
            for group in (self.boost_group, self.bass_group, self.noise_group):
                for b in group.buttons():
                    b.setEnabled(False)
            self.open_button.setEnabled(False)
        elif self.effects.any:
            set_status(self.effects_status, "Effects on: " + ", ".join(audio.describe(self.effects)) + ".", True)
        else:
            self.effects_status.setText("All effects off: sound goes straight to the speakers, with no added latency.")

    def open_easyeffects(self) -> None:
        self._call(audio.open_easyeffects)

    def shutdown(self) -> None:
        if self.busy():
            self._worker.wait()


# --- 09 PERDIX Vision -------------------------------------------------------------------------------


def vision_test_scene(width: int = 240, height: int = 135):
    """A synthetic game-like frame for the preview: a dark corridor with a figure in the shadows,
    a bright window, coloured signs, and grey and hue ramps along the bottom. Values 0-1."""
    import numpy as np
    y, x = np.mgrid[0:height, 0:width] / np.array([height, width])[:, None, None]
    scene = np.zeros((height, width, 3))
    depth = 0.04 + 0.10 * (1 - np.abs(x - 0.5) * 2) * (1 - y)  # dim corridor, darker at the sides
    scene[:] = depth[..., None] * np.array([0.8, 0.85, 1.0])
    window = (x > 0.70) & (x < 0.86) & (y > 0.10) & (y < 0.42)
    scene[window] = [0.95, 0.92, 0.85]
    figure = ((x - 0.33) ** 2 / 0.0025 + (y - 0.44) ** 2 / 0.03) < 1  # someone hiding in the dark
    scene[figure] = [0.07, 0.055, 0.05]
    scene[(x > 0.10) & (x < 0.20) & (y > 0.18) & (y < 0.26)] = [0.75, 0.10, 0.08]  # red sign
    scene[(x > 0.50) & (x < 0.58) & (y > 0.30) & (y < 0.36)] = [0.10, 0.55, 0.30]  # green light
    ramp = y > 0.78
    scene[ramp & (y < 0.89)] = np.repeat(x[ramp & (y < 0.89)][:, None], 3, axis=1)
    hue = x[y >= 0.89] * 6
    scene[y >= 0.89] = np.clip(np.stack([np.abs(hue - 3) - 1, 2 - np.abs(hue - 2), 2 - np.abs(hue - 4)], -1), 0, 1)
    return scene


class VisionPreview(QLabel):
    """The whole test scene before (left) and after (right), computed with the same colour pipeline
    that builds gamescope's LUT."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAlignment(Qt.AlignCenter)
        self._scene = vision_test_scene()
        self._image = None

    def update_preview(self, settings: vision.VisionSettings, lut) -> None:
        import numpy as np
        after = vision.transform(self._scene, settings, lut) if settings.enabled else self._scene
        frame = np.concatenate([self._scene, after], axis=1)
        data = np.ascontiguousarray((np.clip(frame, 0, 1) * 255).astype(np.uint8))
        self._image = QImage(data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format_RGB888).copy()
        self._render()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if self._image is None:
            return
        pixmap = QPixmap.fromImage(self._image).scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p = QPainter(pixmap)
        p.setPen(QPen(qcolor(ORANGE), 2))
        p.drawLine(pixmap.width() // 2, 0, pixmap.width() // 2, pixmap.height())
        p.setPen(qcolor(TEXT))
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Bold)
        p.setFont(font)
        p.drawText(8, 16, "BEFORE")
        p.drawText(pixmap.width() // 2 + 8, 16, "AFTER")
        p.end()
        self.setPixmap(pixmap)


class VisionPage(QWidget):
    """PERDIX Vision: TacticalVision-style picture tuning applied to games by gamescope."""

    SLIDERS = (
        ("brightness", "Brightness", "basic"), ("contrast", "Contrast", "basic"),
        ("vibrance", "Saturation (vibrance)", "basic"), ("sharpening", "Sharpening", "basic"),
        ("lift", "Lift (shadow floor)", "shadow"), ("gamma", "Gamma (mid-tones)", "shadow"),
        ("gain", "Gain (highlights)", "shadow"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self.settings = vision.VisionSettings.from_dict(hub_settings.load().get("vision"))
        self._lut_cache: tuple[str, object] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(PageHeader(module_number("vision"), "Vision", "PERDIX Vision",
                                    "Picture tuning for every game: colour through a gamescope 3D LUT, sharpening "
                                    "through gamescope's ReShade. Changes apply live while a game runs."))

        top = Card()
        row = QHBoxLayout()
        self.enabled = QCheckBox("Vision on: games run in gamescope", cursor=Qt.PointingHandCursor)
        self.enabled.setChecked(self.settings.enabled)
        self.enabled.toggled.connect(self.changed)
        row.addWidget(self.enabled)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        row.addWidget(self.status, 1)
        self.reset_button = button("Reset to neutral")
        self.reset_button.clicked.connect(self.reset)
        row.addWidget(self.reset_button)
        top.body.addLayout(row)
        layout.addWidget(top)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        layout.addLayout(columns, 1)
        left = QVBoxLayout()
        left.setSpacing(16)
        basic, shadow = Card("Basic settings"), Card("Shadow boost  //  lift · gamma · gain")
        self.sliders: dict[str, LabeledSlider] = {}
        for key, label, card in self.SLIDERS:
            low, high = vision.VisionSettings.RANGES[key]
            fmt = (lambda v: f"{v} %") if key == "sharpening" else (lambda v: f"{v:+d}")
            slider = LabeledSlider(label, low, high, getattr(self.settings, key), fmt)
            slider.valueChanged.connect(self.changed)
            self.sliders[key] = slider
            (basic if card == "basic" else shadow).body.addWidget(slider)
        shadow.body.addWidget(QLabel("Lift raises the darkest areas, gamma opens up the mid-tones, gain sets "
                                     "the highlights: enemies in shadows become visible.", objectName="Muted",
                                     wordWrap=True))
        left.addWidget(basic)
        left.addWidget(shadow)
        left.addStretch(1)
        columns.addLayout(left, 5)

        right = QVBoxLayout()
        right.setSpacing(16)
        preview_card = Card("Preview")
        self.preview = VisionPreview()
        preview_card.body.addWidget(self.preview, 1)
        right.addWidget(preview_card, 3)
        lut_card = Card("3D LUT  //  .cube colour profiles")
        lut_row = QHBoxLayout()
        self.lut_list = QListWidget()
        self.lut_list.setMaximumHeight(120)
        self.lut_list.currentRowChanged.connect(self.lut_selected)
        lut_row.addWidget(self.lut_list, 1)
        lut_buttons = QVBoxLayout()
        self.import_button = button("Import .cube…")
        self.import_button.clicked.connect(self.import_clicked)
        self.remove_button = button("Remove")
        self.remove_button.clicked.connect(self.remove_clicked)
        lut_buttons.addWidget(self.import_button)
        lut_buttons.addWidget(self.remove_button)
        lut_buttons.addStretch(1)
        lut_row.addLayout(lut_buttons)
        lut_card.body.addLayout(lut_row)
        self.strength = LabeledSlider("LUT strength", 0, 100, self.settings.lut_strength, lambda v: f"{v} %")
        self.strength.valueChanged.connect(self.changed)
        lut_card.body.addWidget(self.strength)
        right.addWidget(lut_card, 2)
        columns.addLayout(right, 6)

        # Slider moves update the preview at once and reach gamescope a moment after they stop.
        self._apply_timer = QTimer(self, singleShot=True, interval=250, timeout=self.apply)
        self.refresh_luts()
        self.update_preview()
        self.show_status()

    # --- state ---

    def current(self) -> vision.VisionSettings:
        item = self.lut_list.currentItem()
        lut = item.data(Qt.UserRole) if item is not None else ""
        values = {key: slider.value() for key, slider in self.sliders.items()}
        return vision.VisionSettings(enabled=self.enabled.isChecked(), lut=lut or "",
                                     lut_strength=self.strength.value(), **values)

    def lut(self, name: str):
        if not name:
            return None
        if self._lut_cache is None or self._lut_cache[0] != name:
            try:
                self._lut_cache = (name, vision.load_lut(name))
            except vision.VisionError:
                self._lut_cache = (name, None)
        return self._lut_cache[1]

    def refresh_luts(self, select: str | None = None) -> None:
        select = self.settings.lut if select is None else select
        self.lut_list.blockSignals(True)
        self.lut_list.clear()
        none = QListWidgetItem("None")
        none.setData(Qt.UserRole, "")
        self.lut_list.addItem(none)
        for name in vision.list_luts():
            item = QListWidgetItem(name[:-5])
            item.setData(Qt.UserRole, name)
            self.lut_list.addItem(item)
            if name == select:
                self.lut_list.setCurrentItem(item)
        if self.lut_list.currentItem() is None:
            self.lut_list.setCurrentRow(0)
        self.lut_list.blockSignals(False)
        self.remove_button.setEnabled(self.lut_list.currentRow() > 0)

    def changed(self, *_args) -> None:
        self.settings = self.current()
        self.update_preview()
        self._apply_timer.start()

    def update_preview(self) -> None:
        self.preview.update_preview(self.settings, self.lut(self.settings.lut))

    def lut_selected(self, row: int) -> None:
        self.remove_button.setEnabled(row > 0)
        self.changed()

    def reset(self) -> None:
        for slider in self.sliders.values():
            slider.slider.setValue(0)
        self.strength.slider.setValue(100)
        self.lut_list.setCurrentRow(0)

    def show_status(self, message: str = "", ok: bool = True) -> None:
        if message:
            set_status(self.status, message, ok)
        elif not vision.gamescope_installed():
            set_status(self.status, "gamescope is not installed.", False)
        elif vision.session_display():
            set_status(self.status, "Games are running in gamescope: changes apply live.", True)
        elif self.settings.enabled:
            set_status(self.status, "Applied to games as soon as you open Steam.", True)
        else:
            set_status(self.status, "Off: games run without gamescope.", True)

    # --- apply ---

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def apply(self) -> None:
        if self.busy():
            self._apply_timer.start()  # try again when the current run is done
            return
        settings = self.settings
        hub_settings.save_section("vision", settings.to_dict())
        self._worker = Worker(lambda: vision.apply(settings), self)
        self._worker.done.connect(self._applied)
        self._worker.start()

    def _applied(self, result) -> None:
        if isinstance(result, Exception):
            self.show_status(str(result), False)
        else:
            self.show_status(result, True)

    # --- LUT library ---

    def choose_file(self) -> str:
        path, _filter = QFileDialog.getOpenFileName(self, "Import a 3D LUT", os.path.expanduser("~"),
                                                    "3D LUT (*.cube *.CUBE)")
        return path

    def import_clicked(self) -> None:
        path = self.choose_file()
        if not path:
            return
        try:
            name = vision.import_lut(path)
        except vision.VisionError as error:
            self.show_status(str(error), False)
            return
        self.refresh_luts(select=name)
        self.changed()
        self.show_status(f"Imported {name[:-5]}.", True)

    def remove_clicked(self) -> None:
        item = self.lut_list.currentItem()
        name = item.data(Qt.UserRole) if item is not None else ""
        if not name:
            return
        vision.delete_lut(name)
        self.refresh_luts(select="")
        self.changed()
        self.show_status(f"Removed {name[:-5]}.", True)

    def shutdown(self) -> None:
        if self._apply_timer.isActive():
            self._apply_timer.stop()
            self.apply()
        if self.busy():
            self._worker.wait()


# --- Task Manager ------------------------------------------------------------------------------------


class UsageGraph(QWidget):
    """A caption, the current value and the last minute as a line (one sample every 2 s)."""

    HISTORY = 30

    def __init__(self, caption: str, color: str, parent=None) -> None:
        super().__init__(parent)
        self.caption, self.color = caption, color
        self.samples: list[float] = []
        self.value_text, self.detail = "—", ""
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def add(self, percent: float | None, value_text: str, detail: str = "") -> None:
        if percent is not None:
            self.samples = (self.samples + [max(0.0, min(100.0, percent))])[-self.HISTORY:]
        self.value_text, self.detail = value_text, detail
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillRect(rect, qcolor(FIELD))
        p.setPen(QPen(qcolor(LINE), 1))
        p.drawRect(rect)
        graph = rect.adjusted(10, 46, -10, -8)
        for frac in (0.5,):
            p.drawLine(QPointF(graph.left(), graph.top() + graph.height() * frac),
                       QPointF(graph.right(), graph.top() + graph.height() * frac))
        if len(self.samples) > 1:
            step = graph.width() / (self.HISTORY - 1)
            start = graph.right() - step * (len(self.samples) - 1)
            line = QPainterPath()
            for i, value in enumerate(self.samples):
                point = QPointF(start + i * step, graph.bottom() - graph.height() * value / 100)
                line.lineTo(point) if i else line.moveTo(point)
            area = QPainterPath(line)
            area.lineTo(graph.right(), graph.bottom())
            area.lineTo(start, graph.bottom())
            area.closeSubpath()
            p.fillPath(area, qcolor(self.color, 0.14))
            p.strokePath(line, QPen(qcolor(self.color), 2))
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        p.setFont(font)
        p.setPen(qcolor(MUTED))
        p.drawText(QRectF(10, 8, rect.width() - 20, 14), Qt.AlignLeft | Qt.AlignVCenter, self.caption.upper())
        p.drawText(QRectF(10, 8, rect.width() - 20, 14), Qt.AlignRight | Qt.AlignVCenter, self.detail)
        mono = QFont(self.font())
        mono.setFamily("DejaVu Sans Mono")
        mono.setPixelSize(20)
        mono.setWeight(QFont.DemiBold)
        p.setFont(mono)
        p.setPen(qcolor(self.color))
        p.drawText(QRectF(10, 22, rect.width() - 20, 24), Qt.AlignLeft | Qt.AlignVCenter, self.value_text)


class TaskManagerPage(QWidget):
    """CPU, RAM and GPU use, running programs, and ending them. Refreshes every 2 s while visible."""

    COLUMNS = ("Name", "Status", "CPU", "Memory", "PID")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self._monitor: taskman.TaskMonitor | None = None
        self.processes: list[taskman.ProcessInfo] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(PageHeader(module_number("taskman"), "System", "Task Manager",
                                    "What your PC is busy with right now. Programs that stop answering are "
                                    "marked Not responding and can be killed with one click."))

        graphs = QHBoxLayout()
        graphs.setSpacing(16)
        self.cpu_graph = UsageGraph("CPU", ORANGE)
        self.ram_graph = UsageGraph("Memory", ICE)
        self.gpu_graph = UsageGraph("GPU", "#b388ff")
        for g in (self.cpu_graph, self.ram_graph, self.gpu_graph):
            graphs.addWidget(g)
        layout.addLayout(graphs)

        self.hung_banner = QFrame(objectName="Card")
        self.hung_banner.setStyleSheet(f"QFrame#Card {{ border: 1px solid {DANGER}; background: {_rgba(DANGER, 28)}; }}")
        banner = QHBoxLayout(self.hung_banner)
        banner.setContentsMargins(16, 10, 10, 10)
        self.hung_label = QLabel(wordWrap=True)
        self.hung_label.setStyleSheet(f"color: {DANGER}; font-weight: 700;")
        banner.addWidget(self.hung_label, 1)
        self.hung_kill = primary_button("Kill now")
        self.hung_kill.clicked.connect(self.kill_hung)
        banner.addWidget(self.hung_kill)
        self.hung_banner.hide()
        layout.addWidget(self.hung_banner)

        card = Card("Running programs")
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([c.upper() for c in self.COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        # Default order: hung programs, then programs with a window, then the rest by CPU. A click on a
        # column header sorts by that column instead (again to reverse).
        self.sort_column: int | None = None
        self.sort_descending = True
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self.header_clicked)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(220)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        card.body.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.all_users = QCheckBox("Show system processes", cursor=Qt.PointingHandCursor)
        self.all_users.toggled.connect(lambda _on: self.refresh())
        actions.addWidget(self.all_users)
        actions.addStretch(1)
        self.end_button = primary_button("End task")
        self.end_button.clicked.connect(lambda: self.end_selected(force=False))
        self.kill_button = button("Kill")
        self.kill_button.setToolTip("Stop it at once (SIGKILL); unsaved work in it is lost")
        self.kill_button.clicked.connect(lambda: self.end_selected(force=True))
        actions.addWidget(self.end_button)
        actions.addWidget(self.kill_button)
        card.body.addLayout(actions)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        card.body.addWidget(self.status)
        layout.addWidget(card, 1)

        self._timer = QTimer(self, interval=2000, timeout=self.refresh)
        self.update_buttons()

    # --- refresh ---

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def monitor(self) -> taskman.TaskMonitor:
        if self._monitor is None:
            self._monitor = taskman.TaskMonitor()
        return self._monitor

    def refresh(self) -> None:
        if self.busy():
            return
        monitor, all_users = self.monitor(), self.all_users.isChecked()
        self._worker = Worker(lambda: monitor.snapshot(all_users), self)
        self._worker.done.connect(self.show_snapshot)
        self._worker.start()

    def show_snapshot(self, snap) -> None:
        if isinstance(snap, Exception):
            set_status(self.status, f"Could not read the process list: {snap}", False)
            return
        u = snap.usage
        cores = len(u.per_core)
        self.cpu_graph.add(u.cpu, f"{u.cpu:.0f} %", f"{cores} cores")
        self.ram_graph.add(u.memory_percent, f"{u.memory_percent:.0f} %",
                           f"{taskman.human_bytes(u.memory_used)} / {taskman.human_bytes(u.memory_total)}")
        gpu = u.gpus[0] if u.gpus else None
        if gpu is None:
            self.gpu_graph.add(None, "n/a", "no load reported")
        else:
            vram = (f"VRAM {taskman.human_bytes(gpu.vram_used)} / {taskman.human_bytes(gpu.vram_total)}"
                    if gpu.vram_total else gpu.name)
            self.gpu_graph.add(gpu.load, f"{gpu.load:.0f} %" if gpu.load is not None else "n/a", vram)
        self.fill_table(snap.processes)
        hung = [p for p in snap.processes if p.responding is False]
        if hung:
            names = ", ".join(p.window_title for p in hung)
            self.hung_label.setText(f"{names} {'is' if len(hung) == 1 else 'are'} not responding.")
            self.hung_kill.setProperty("pids", [p.pid for p in hung])
            self.hung_banner.show()
        else:
            self.hung_banner.hide()

    def fill_table(self, procs: list[taskman.ProcessInfo]) -> None:
        selected = self.selected()
        keep = selected.pid if selected else None
        self.processes = sorted(procs, key=lambda p: (p.responding is not False, not p.is_app, -p.cpu, p.name))
        if self.sort_column is not None:
            key = (lambda p: (p.window_title or p.name).lower(), lambda p: self.status_text(p),
                   lambda p: p.cpu, lambda p: p.memory, lambda p: p.pid)[self.sort_column]
            self.processes.sort(key=key, reverse=self.sort_descending)
        self.table.setRowCount(len(self.processes))
        for row, p in enumerate(self.processes):
            name = p.window_title or p.name
            cells = tuple(QTableWidgetItem(text) for text in (name, self.status_text(p), f"{p.cpu:.1f} %",
                                                                 taskman.human_bytes(p.memory), str(p.pid)))
            cells[0].setData(Qt.UserRole, p.pid)
            cells[0].setToolTip(f"{p.name} (PID {p.pid}, {p.user})")
            if p.responding is False:
                for item in cells:
                    item.setForeground(qcolor(DANGER))
            elif p.protected:
                cells[0].setForeground(qcolor(MUTED))
            elif p.is_app:
                cells[0].setForeground(qcolor(ICE))
            for column, item in enumerate(cells):
                self.table.setItem(row, column, item)
        if keep is not None:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.UserRole) == keep:
                    self.table.selectRow(row)
                    break
        self.update_buttons()

    @staticmethod
    def status_text(p: taskman.ProcessInfo) -> str:
        if p.responding is False:
            return "Not responding"
        if p.is_app:
            return "Running"
        return "Background" if p.status in ("sleeping", "running", "idle") else p.status.capitalize()

    def header_clicked(self, column: int) -> None:
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column, self.sort_descending = column, column in (2, 3)  # CPU and memory: largest first
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(column, Qt.DescendingOrder if self.sort_descending
                                                       else Qt.AscendingOrder)
        self.fill_table(self.processes)

    # --- ending ---

    def selected(self) -> taskman.ProcessInfo | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        pid = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        return next((p for p in self.processes if p.pid == pid), None)

    def update_buttons(self) -> None:
        p = self.selected()
        can_end = p is not None and not p.protected and not self.busy()
        self.end_button.setEnabled(can_end)
        self.kill_button.setEnabled(can_end)
        if p is not None and p.protected:
            self.status.setText(f"{p.name} keeps PERDIX OS running and can't be ended here.")

    def end_selected(self, force: bool) -> None:
        p = self.selected()
        if p is not None and not p.protected:
            self._end([p.pid], force or p.responding is False, p.window_title or p.name)

    def kill_hung(self) -> None:
        pids = self.hung_kill.property("pids") or []
        self._end(pids, True, self.hung_label.text().split(" is not")[0].split(" are not")[0])

    def _end(self, pids: list[int], force: bool, name: str) -> None:
        if self.busy():
            self._worker.wait()
        set_status(self.status, f"{'Killing' if force else 'Ending'} {name}…", True)

        def work():
            return [taskman.end_task(pid, force) for pid in pids]
        self._worker = Worker(work, self)
        self._worker.done.connect(lambda result: self._ended(result, name, force))
        self._worker.start()
        self.update_buttons()

    def _ended(self, result, name: str, force: bool) -> None:
        if isinstance(result, Exception):
            set_status(self.status, str(result), False)
        else:
            set_status(self.status, f"{name} was {'killed' if force else 'ended'}.", True)
        self.hung_banner.hide()
        self.refresh()

    def shutdown(self) -> None:
        self._timer.stop()
        if self.busy():
            self._worker.wait()
        if self._monitor is not None:
            self._monitor.close()


# --- Devices ------------------------------------------------------------------------------------------


class DevicesPage(QWidget):
    """Motherboard, the PC's parts with their drivers, input devices and USB devices."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self.inventory: devices.Inventory | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(PageHeader(module_number("devices"), "System", "Devices",
                                    "Every part inside and plugged into your PC, and the Linux driver that runs it. "
                                    "Parts without a driver are marked."))

        board = Card("Motherboard")
        row = QHBoxLayout()
        self.board_value = Readout("Board", small=True)
        self.bios_value = Readout("BIOS", small=True)
        self.system_value = Readout("System", small=True)
        for r in (self.board_value, self.bios_value, self.system_value):
            row.addWidget(r, 1)
        self.refresh_button = button("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button, 0, Qt.AlignTop)
        board.body.addLayout(row)
        layout.addWidget(board)

        self.warning = QLabel(wordWrap=True)
        self.warning.setStyleSheet(f"color: {DANGER}; font-weight: 600;")
        self.warning.hide()
        layout.addWidget(self.warning)

        card = Card("Hardware and drivers")
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["DEVICE", "DRIVER", "ID / LOCATION"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.setMinimumHeight(300)
        card.body.addWidget(self.tree, 1)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        card.body.addWidget(self.status)
        layout.addWidget(card, 1)
        self.loaded = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.loaded:
            self.refresh()

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def refresh(self) -> None:
        if self.busy():
            return
        self.loaded = True
        self.refresh_button.setEnabled(False)
        self.status.setText("Reading the hardware list…")
        self._worker = Worker(devices.inventory, self)
        self._worker.done.connect(self.show_inventory)
        self._worker.start()

    def show_inventory(self, inventory) -> None:
        self.refresh_button.setEnabled(True)
        if isinstance(inventory, Exception):
            set_status(self.status, f"Could not read the hardware list: {inventory}", False)
            return
        self.inventory = inventory
        b = inventory.board
        self.board_value.set(" ".join(x for x in (b.vendor, b.model) if x) or "Unknown")
        self.bios_value.set(" ".join(x for x in (b.bios_version, f"({b.bios_date})" if b.bios_date else "") if x)
                            or "Unknown")
        self.system_value.set(b.system or "Unknown")
        self.tree.clear()
        for category, items in inventory.by_category():
            group = QTreeWidgetItem([f"{category}  ({len(items)})", "", ""])
            group.setForeground(0, qcolor(ORANGE))
            font = QFont(group.font(0))
            font.setBold(True)
            group.setFont(0, font)
            self.tree.addTopLevelItem(group)
            for d in items:
                name = f"{d.name}  —  {d.vendor}" if d.vendor else d.name
                driver = d.driver or ("none (possible: " + ", ".join(d.modules) + ")" if d.modules else "none")
                item = QTreeWidgetItem([name, driver, "  ".join(x for x in (d.ids, d.location) if x)])
                item.setToolTip(0, name)
                if d.has_driver:
                    item.setForeground(1, qcolor(ICE))
                elif d in inventory.missing_drivers:
                    for column in range(3):
                        item.setForeground(column, qcolor(DANGER))
                else:
                    item.setForeground(1, qcolor(MUTED))
                group.addChild(item)
            group.setExpanded(category != "Other")
        missing = inventory.missing_drivers
        if missing:
            names = ", ".join(d.name for d in missing)
            self.warning.setText(f"No driver in use for: {names}. These parts may not work until a driver "
                                 "is installed (for NVIDIA cards: build the ISO with --nvidia).")
            self.warning.show()
        else:
            self.warning.hide()
        count = len(inventory.devices)
        set_status(self.status, f"{count} devices found." + ("" if missing else " Every main part has a driver."),
                   True)

    def shutdown(self) -> None:
        if self.busy():
            self._worker.wait()


# --- PERDIX System Diagnostics -------------------------------------------------------------------------


class DiagnosticsPage(QWidget):
    """One report: GPU, Vulkan, OpenGL, DirectX through Proton, RAM, BIOS, displays, disks."""

    SUMMARY = ("GPU", "Vulkan", "DirectX", "RAM", "BIOS")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self.report: diagnostics.Report | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(PageHeader(module_number("diagnostics"), "System", "PERDIX System Diagnostics",
                                    "What this PC is and what games can use: graphics, Vulkan, DirectX through "
                                    "Proton, memory, BIOS. Save the report to send it with a support question."))

        summary = Card("Summary")
        row = QHBoxLayout()
        self.summary = {}
        for key in self.SUMMARY:
            readout = Readout(key, small=True)
            readout.value.setWordWrap(True)
            self.summary[key] = readout
            row.addWidget(readout, 2 if key == "GPU" else 1)
        summary.body.addLayout(row)
        layout.addWidget(summary)

        self.problems = QLabel(wordWrap=True)
        self.problems.setStyleSheet(f"color: {DANGER}; font-weight: 600;")
        self.problems.hide()
        layout.addWidget(self.problems)

        card = Card("Report")
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.setMinimumHeight(300)
        card.body.addWidget(self.tree, 1)
        actions = QHBoxLayout()
        self.status = QLabel(objectName="Muted", wordWrap=True)
        actions.addWidget(self.status, 1)
        self.run_button = button("Run again")
        self.run_button.clicked.connect(self.run)
        self.copy_button = button("Copy")
        self.copy_button.clicked.connect(self.copy)
        self.save_button = primary_button("Save report")
        self.save_button.clicked.connect(self.save)
        for b in (self.run_button, self.copy_button, self.save_button):
            actions.addWidget(b)
        card.body.addLayout(actions)
        layout.addWidget(card, 1)
        self.loaded = False
        self.update_buttons()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.loaded:
            self.run()

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def update_buttons(self) -> None:
        for b in (self.copy_button, self.save_button):
            b.setEnabled(self.report is not None and not self.busy())
        self.run_button.setEnabled(not self.busy())

    def run(self) -> None:
        if self.busy():
            return
        self.loaded = True
        set_status(self.status, "Running diagnostics (Vulkan, OpenGL, devices)…", True)
        self._worker = Worker(diagnostics.build_report, self)
        self._worker.done.connect(self.show_report)
        self._worker.start()
        self.update_buttons()

    def show_report(self, report) -> None:
        if isinstance(report, Exception):
            set_status(self.status, f"Diagnostics failed: {report}", False)
            self.update_buttons()
            return
        self.report = report
        for key, readout in self.summary.items():
            readout.set(report.summary.get(key, "—"))
        self.tree.clear()
        for title, rows in report.sections:
            heading = QTreeWidgetItem([title.upper(), ""])
            heading.setForeground(0, qcolor(ORANGE))
            font = QFont(heading.font(0))
            font.setBold(True)
            heading.setFont(0, font)
            self.tree.addTopLevelItem(heading)
            for r in rows:
                item = QTreeWidgetItem(["    " + r.key, r.value])
                item.setToolTip(1, r.value)
                color = DANGER if r.ok is False else ICE if r.ok else TEXT
                item.setForeground(1, qcolor(color))
                self.tree.addTopLevelItem(item)
        problems = report.problems()
        if problems:
            self.problems.setText("To look at: " + "; ".join(f"{r.key}: {r.value}" for r in problems[:3])
                                  + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""))
            self.problems.show()
        else:
            self.problems.hide()
        set_status(self.status, f"Report from {report.created}.", True)
        self.update_buttons()

    def copy(self) -> None:
        if self.report is not None:
            QApplication.clipboard().setText(diagnostics.report_text(self.report))
            set_status(self.status, "Report copied.", True)

    def save(self) -> None:
        if self.report is None:
            return
        try:
            path = diagnostics.save_report(self.report)
        except OSError as error:
            set_status(self.status, f"Could not save: {error}", False)
            return
        set_status(self.status, f"Saved to {home_path(path)} (in My Files).", True)

    def shutdown(self) -> None:
        if self.busy():
            self._worker.wait()


# --- Region & Language ---------------------------------------------------------------------------------


class RegionPage(QWidget):
    """Time zone and internet time, keyboard layout(s), system language."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self.state: region.RegionStatus | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(PageHeader(module_number("region"), "System", "Region & Language",
                                    "Where you are, how you type and which language programs use."))

        time_card = Card("Date and time")
        row = QHBoxLayout()
        self.clock = Readout("Local time")
        self.zone_value = Readout("Time zone", small=True)
        self.sync_value = Readout("Internet time", small=True)
        for r in (self.clock, self.zone_value, self.sync_value):
            row.addWidget(r, 1)
        time_card.body.addLayout(row)
        zone_row = QHBoxLayout()
        self.zone = QComboBox(editable=True, insertPolicy=QComboBox.NoInsert)
        self.zone.completer().setFilterMode(Qt.MatchContains)
        self.zone.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.zone.setMinimumWidth(320)
        self.zone_button = button("Set time zone")
        self.zone_button.clicked.connect(self.set_zone)
        self.ntp = QCheckBox("Set the time automatically from the internet", cursor=Qt.PointingHandCursor)
        self.ntp.clicked.connect(lambda on: self._run(lambda: region.set_ntp(on)))
        zone_row.addWidget(self.zone, 1)
        zone_row.addWidget(self.zone_button)
        zone_row.addSpacing(16)
        zone_row.addWidget(self.ntp)
        time_card.body.addLayout(zone_row)
        layout.addWidget(time_card)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        keyboard = Card("Keyboard")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.primary = QComboBox()
        self.secondary = QComboBox()
        self.secondary.addItem("None", None)
        for lay, var, name in region.KEYBOARDS:
            self.primary.addItem(name, (lay, var))
            self.secondary.addItem(name, (lay, var))
        form.addWidget(QLabel("Layout"), 0, 0)
        form.addWidget(self.primary, 0, 1)
        form.addWidget(QLabel("Second layout"), 1, 0)
        form.addWidget(self.secondary, 1, 1)
        form.setColumnStretch(1, 1)
        keyboard.body.addLayout(form)
        keyboard.body.addWidget(QLabel("With a second layout, Alt+Shift switches between the two, like on "
                                       "Windows. Türkçe F is the F-keyboard; Türkçe Q the common one.",
                                       objectName="Muted", wordWrap=True))
        self.session_keyboard = QLabel(objectName="Muted", wordWrap=True)
        keyboard.body.addWidget(self.session_keyboard)
        self.keyboard_button = primary_button("Apply keyboard")
        self.keyboard_button.clicked.connect(self.apply_keyboard)
        keyboard.body.addWidget(self.keyboard_button, 0, Qt.AlignLeft)
        keyboard.body.addStretch(1)
        columns.addWidget(keyboard, 1)

        language = Card("Language")
        self.language = QComboBox()
        language.body.addWidget(self.language)
        language.body.addWidget(QLabel("The language of menus in Steam, games and other programs. It applies "
                                       "after the next login; PERDIX's own screens stay as they are.",
                                       objectName="Muted", wordWrap=True))
        self.language_button = button("Apply language")
        self.language_button.clicked.connect(self.apply_language)
        language.body.addWidget(self.language_button, 0, Qt.AlignLeft)
        language.body.addStretch(1)
        columns.addWidget(language, 1)
        layout.addLayout(columns)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        layout.addWidget(self.status)
        layout.addStretch(1)

        self._clock = QTimer(self, interval=1000, timeout=self._tick)
        self.loaded = False

    def _tick(self) -> None:
        self.clock.set(QDateTime.currentDateTime().toString("HH:mm:ss"))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._tick()
        self._clock.start()
        if not self.loaded:
            self.load()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._clock.stop()

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def load(self) -> None:
        if self.busy():
            return
        self.loaded = True

        def work():
            return region.status(), region.timezones(), region.locales()
        self._worker = Worker(work, self)
        self._worker.done.connect(self.show_state)
        self._worker.start()

    def show_state(self, result) -> None:
        if isinstance(result, Exception):
            set_status(self.status, str(result), False)
            for b in (self.zone_button, self.keyboard_button, self.language_button, self.ntp):
                b.setEnabled(False)
            return
        state, zones, locales = result
        self.state = state
        t = state.time
        self.zone_value.set(t.timezone.replace("_", " ") or "—")
        self.sync_value.set("SYNCED" if t.ntp_synced else "ON" if t.ntp else "OFF")
        if self.zone.count() != len(zones):
            self.zone.clear()
            self.zone.addItems(zones)
        self.zone.setCurrentText(t.timezone)
        self.ntp.setChecked(t.ntp)
        self.ntp.setEnabled(t.can_ntp)
        self.ntp.setToolTip("" if t.can_ntp else "No network time service on this system")
        keyboard = state.session or state.system
        if keyboard.layouts:
            variants = keyboard.variants + [""] * len(keyboard.layouts)
            self._select(self.primary, (keyboard.layouts[0], variants[0]))
            self._select(self.secondary, (keyboard.layouts[1], variants[1]) if len(keyboard.layouts) > 1 else None)
            names = " + ".join(region.keyboard_name(lay, var) for lay, var in zip(keyboard.layouts, variants))
            self.session_keyboard.setText(f"Now: {names}")
        self.language.clear()
        for locale in locales:
            self.language.addItem(f"{region.language_name(locale)}  ({locale})", locale)
            if locale == state.system.lang:
                self.language.setCurrentIndex(self.language.count() - 1)
        self.language_button.setEnabled(len(locales) > 1)

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _run(self, func) -> None:
        if self.busy():
            return
        set_status(self.status, "Applying…", True)
        self._worker = Worker(func, self)
        self._worker.done.connect(self._done)
        self._worker.start()

    def _done(self, result) -> None:
        if isinstance(result, Exception):
            set_status(self.status, str(result), False)
        else:
            set_status(self.status, result, True)
        self.loaded = False
        self.load()

    def set_zone(self) -> None:
        zone = self.zone.currentText().strip().replace(" ", "_")
        self._run(lambda: region.set_timezone(zone))

    def apply_keyboard(self) -> None:
        primary, secondary = self.primary.currentData(), self.secondary.currentData()
        self._run(lambda: region.set_keyboard(primary, secondary))

    def apply_language(self) -> None:
        locale = self.language.currentData()
        if locale:
            self._run(lambda: region.set_language(locale))

    def shutdown(self) -> None:
        self._clock.stop()
        if self.busy():
            self._worker.wait()


# --- Bluetooth -------------------------------------------------------------------------------------------


class BluetoothPage(QWidget):
    """Scan, pair, connect and forget Bluetooth devices."""

    COLUMNS = ("Device", "Type", "Status", "Battery")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self.adapter: bluetooth.Adapter | None = None
        self.devices: list[bluetooth.Device] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(PageHeader(module_number("bluetooth"), "Network & Sound", "Bluetooth",
                                    "Headphones, speakers, game controllers, mice and keyboards. Pair once; they "
                                    "reconnect by themselves when switched on."))

        top = Card("Adapter")
        row = QHBoxLayout()
        self.adapter_value = Readout("Adapter", small=True)
        self.power_value = Readout("Bluetooth", small=True)
        self.connected_value = Readout("Connected", small=True)
        for r in (self.adapter_value, self.power_value, self.connected_value):
            row.addWidget(r, 1)
        self.power = QCheckBox("Bluetooth on", cursor=Qt.PointingHandCursor)
        self.power.clicked.connect(lambda on: self._run(lambda: bluetooth.set_powered(on), "Switching…"))
        row.addWidget(self.power)
        top.body.addLayout(row)
        layout.addWidget(top)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        card = Card("Devices")
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([c.upper() for c in self.COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(240)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self.primary_action())
        card.body.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.scan_button = primary_button("Scan for devices")
        self.scan_button.clicked.connect(self.scan)
        self.action_button = button("Pair and connect")
        self.action_button.clicked.connect(self.primary_action)
        self.forget_button = button("Forget")
        self.forget_button.clicked.connect(self.forget)
        self.blueman_button = button("Blueman…")
        self.blueman_button.setToolTip("Blueman: every Bluetooth setting, file transfer, PIN pairing")
        self.blueman_button.clicked.connect(self.open_blueman)
        for b in (self.scan_button, self.action_button, self.forget_button):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(self.blueman_button)
        card.body.addLayout(actions)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        card.body.addWidget(self.status)
        columns.addWidget(card, 7)

        tips = Card("Pairing mode")
        for title, text in (
            ("PlayStation", "DualSense / DualShock 4: hold Create (Share) and the PS button until the light "
                            "bar flashes quickly."),
            ("Xbox", "Hold the pair button on top of the controller until the Xbox button flashes fast."),
            ("Switch Pro", "Hold the small sync button on top until the lights run back and forth."),
            ("Headphones", "Usually: hold the power or Bluetooth button until the light flashes blue/red. "
                           "Sound switches to them by itself once connected."),
        ):
            tips.body.addWidget(tracked_label(title, "EyebrowOrange", 2))
            tips.body.addWidget(QLabel(text, objectName="Muted", wordWrap=True))
        tips.body.addStretch(1)
        columns.addWidget(tips, 3)
        layout.addLayout(columns, 1)

        self._timer = QTimer(self, interval=8000, timeout=self.refresh)
        self.update_buttons()

    # --- state ---

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def refresh(self) -> None:
        if self.busy():
            return

        def work():
            found = bluetooth.adapter()
            return found, bluetooth.devices() if found else []
        self._worker = Worker(work, self)
        self._worker.done.connect(self.show_state)
        self._worker.start()

    def finished_worker(self) -> None:
        """The worker that sent this result is only returning now; let it end so busy() is honest."""
        worker = self.sender()
        if isinstance(worker, Worker):
            worker.wait()

    def show_state(self, result) -> None:
        self.finished_worker()
        if isinstance(result, Exception):
            self.adapter, self.devices = None, []
            set_status(self.status, str(result), False)
        else:
            self.adapter, self.devices = result
            if self.adapter is None:
                set_status(self.status, "No Bluetooth adapter found. Plug in a USB Bluetooth adapter.", False)
        a = self.adapter
        self.adapter_value.set(a.name if a else "NONE")
        self.power_value.set(("ON" if a.powered else "OFF") if a else "—")
        self.connected_value.set(str(sum(d.connected for d in self.devices)))
        self.power.setChecked(bool(a and a.powered))
        self.power.setEnabled(a is not None)
        self.fill_table()

    def fill_table(self) -> None:
        selected = self.selected()
        keep = selected.address if selected else None
        self.table.setRowCount(len(self.devices))
        for row, d in enumerate(self.devices):
            status = "Connected" if d.connected else "Paired" if d.paired else "Available"
            cells = (d.name, d.kind, status, f"{d.battery} %" if d.battery is not None else "")
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.UserRole, d.address)
                    item.setToolTip(d.address)
                if column == 2 and d.connected:
                    item.setForeground(qcolor(ICE))
                elif column == 2 and d.paired:
                    item.setForeground(qcolor(ORANGE))
                self.table.setItem(row, column, item)
            if d.address == keep:
                self.table.selectRow(row)
        self.update_buttons()

    def selected(self) -> bluetooth.Device | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        address = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        return next((d for d in self.devices if d.address == address), None)

    def update_buttons(self) -> None:
        d, idle = self.selected(), not self.busy()
        powered = bool(self.adapter and self.adapter.powered)
        self.scan_button.setEnabled(idle and powered)
        self.action_button.setEnabled(idle and powered and d is not None)
        self.action_button.setText("Disconnect" if d and d.connected else "Connect" if d and d.paired
                                   else "Pair and connect")
        self.forget_button.setEnabled(idle and d is not None and d.paired)

    # --- actions ---

    def _run(self, func, busy: str) -> None:
        if self.busy():
            self._worker.wait()
        set_status(self.status, busy, True)
        self._worker = Worker(func, self)
        self._worker.done.connect(self._done)
        self._worker.start()
        self.update_buttons()

    def _done(self, result) -> None:
        self.finished_worker()
        if isinstance(result, Exception):
            set_status(self.status, str(result), False)
        elif isinstance(result, list):  # a scan returns the device list
            self.devices = result
            new = sum(not d.paired for d in result)
            set_status(self.status, f"Scan finished: {new} device{'s' if new != 1 else ''} to pair."
                       if new else "Scan finished: no new devices. Is the device in pairing mode?", True)
            self.fill_table()
        else:
            set_status(self.status, result, True)
        self.update_buttons()
        if not isinstance(result, list):
            self.refresh()

    def scan(self) -> None:
        self._run(lambda: bluetooth.scan(), f"Scanning for {bluetooth.SCAN_SECONDS} seconds… put the device in "
                                             "pairing mode now.")

    def primary_action(self) -> None:
        d = self.selected()
        if d is None or not (self.adapter and self.adapter.powered):
            return
        if d.connected:
            self._run(lambda: bluetooth.disconnect(d.address), f"Disconnecting {d.name}…")
        elif d.paired:
            self._run(lambda: bluetooth.connect(d.address), f"Connecting {d.name}…")
        else:
            self._run(lambda: bluetooth.pair(d.address), f"Pairing with {d.name}…")

    def forget(self) -> None:
        d = self.selected()
        if d is not None and d.paired:
            self._run(lambda: bluetooth.remove(d.address), f"Forgetting {d.name}…")

    def open_blueman(self) -> None:
        try:
            bluetooth.open_blueman()
        except bluetooth.BluetoothError as error:
            set_status(self.status, str(error), False)

    def shutdown(self) -> None:
        self._timer.stop()
        if self.busy():
            self._worker.wait()


# --- 07 PERDIX Kılavuz -----------------------------------------------------------------------------


def turkish_upper(text: str) -> str:
    """Upper case with Turkish rules: i -> İ, ı -> I (str.upper() would turn "Bilgi" into "BILGI")."""
    return text.replace("i", "İ").replace("ı", "I").upper()


SEVERITY_COLORS = {"yüksek": DANGER, "orta": ORANGE, "düşük": ICE, "bilgi": MUTED}
CATEGORY_NAMES = {"port": "Port", "izin": "Dosya izni"}


class SecurityPage(QWidget):
    """PERDIX Kılavuz: scan results and the "Açıkları Kapat" button. All text is Turkish."""

    COLUMNS = ("Önem", "Tür", "Hedef", "Bulgu", "Düzeltme")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self.report = None  # perdix_kilavuz.AuditReport of the last scan
        self.scanned = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(PageHeader(
            module_number("security"), turkish_upper("Güvenlik"), turkish_upper("PERDIX Kılavuz"),
            "Açık portları ve zayıf dosya izinlerini denetler. Güvenle düzeltilebilenler "
            "\"Açıkları Kapat\" ile tek tuşla kapatılır; geri kalanlar ne yapmanız gerektiğiyle "
            "birlikte listelenir.",
            module_word="MODÜL"))

        summary = Card(turkish_upper("Durum"))
        row = QHBoxLayout()
        self.counts = {}
        for severity in ("yüksek", "orta", "düşük", "bilgi"):
            readout = Readout(turkish_upper(severity), small=True)
            readout.value.setStyleSheet(f"color: {SEVERITY_COLORS[severity]};")
            self.counts[severity] = readout
            row.addWidget(readout)
        self.last_scan = Readout(turkish_upper("Son tarama"), small=True)
        row.addWidget(self.last_scan)
        row.addStretch(1)
        self.scan_button = button("Taramayı Başlat")
        self.scan_button.clicked.connect(self.start_scan)
        self.close_button = primary_button("Açıkları Kapat")
        self.close_button.setMinimumHeight(44)
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.close_clicked)
        row.addWidget(self.scan_button)
        row.addWidget(self.close_button)
        summary.body.addLayout(row)
        self.status = QLabel("Tarama henüz yapılmadı.", objectName="Muted", wordWrap=True)
        summary.body.addWidget(self.status)
        layout.addWidget(summary)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        layout.addLayout(columns, 1)

        findings = Card(turkish_upper("Bulgular"))
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([turkish_upper(c) for c in self.COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideMiddle)  # long paths keep their file name visible
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        # Target and finding share the free width; the short columns fit their content.
        for column, mode in enumerate((QHeaderView.ResizeToContents, QHeaderView.ResizeToContents,
                                       QHeaderView.Stretch, QHeaderView.Stretch,
                                       QHeaderView.ResizeToContents)):
            header.setSectionResizeMode(column, mode)
        self.table.setMinimumHeight(150)
        self.table.itemSelectionChanged.connect(self.show_details)
        findings.body.addWidget(self.table, 1)
        # The selected finding in full: the whole path, the finding and what to do about it.
        self.details = QLabel("Ayrıntılar için tablodan bir bulgu seçin.", objectName="Muted", wordWrap=True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.details.setMinimumHeight(64)
        self.details.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        findings.body.addWidget(self.details)
        columns.addWidget(findings, 7)

        explain = Card(turkish_upper("Açıkları Kapat ne yapar?"))
        # Scrolls instead of squeezing the text together in a small window.
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        explain_body = QWidget()
        explain_layout = QVBoxLayout(explain_body)
        explain_layout.setContentsMargins(0, 0, 6, 0)
        explain_layout.setSpacing(12)
        scroll.setWidget(explain_body)
        explain.body.addWidget(scroll, 1)
        for title, text in (
            ("İzinler", "Herkesin yazabildiği dosya ve klasörlerden bu yetki kaldırılır. /etc/shadow, "
                        "sudoers, SSH anahtarları ve PERDIX ayarları gibi hassas dosyalar sıkılaştırılır "
                        "ve root'a geri verilir."),
            ("Elle", "Ağa açık servisler ve beklenmeyen SUID programlar otomatik değiştirilmez: bir "
                     "servisi durdurmak SSH'yi ya da Steam'i kesebilir. Bunlar tabloda, ne yapmanız "
                     "gerektiğiyle birlikte kalır."),
            ("Güvenlik", "Değişiklikleri perdix-tune root olarak yapar: klasörleri kendisi yeniden tarar "
                         "ve yalnızca taradığı dosyanın kendisini değiştirir. Arada değiştirilen "
                         "dosyalara dokunmaz."),
            ("Kayıt", "Her değişiklik eski ve yeni izinleriyle /var/log/perdix/kilavuz.log dosyasına "
                      "yazılır; gerekirse geri alınabilir."),
        ):
            explain_layout.addWidget(tracked_label(turkish_upper(title), "EyebrowOrange", 2))
            explain_layout.addWidget(QLabel(text, objectName="Muted", wordWrap=True))
        explain_layout.addStretch(1)
        columns.addWidget(explain, 3)

        if kilavuz.module_path() is None:
            self.scan_button.setEnabled(False)
            set_status(self.status, f"PERDIX Kılavuz bulunamadı ({kilavuz.INSTALLED}).", False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.scanned and self.scan_button.isEnabled():
            self.start_scan()  # first visit: scan right away

    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _start(self, func, done, message: str) -> None:
        self.scan_button.setEnabled(False)
        self.close_button.setEnabled(False)
        set_status(self.status, message, True)
        self._worker = Worker(func, self)
        self._worker.done.connect(done)
        self._worker.start()

    def start_scan(self) -> None:
        if self.busy():
            return
        self.scanned = True
        self._start(kilavuz.scan, self.show_report, "Taranıyor: açık portlar ve dosya izinleri…")

    def show_report(self, report) -> None:
        self.scan_button.setEnabled(True)
        if isinstance(report, Exception):
            set_status(self.status, f"Tarama başarısız: {report}", False)
            return
        self.report = report
        findings = self.findings = report.sorted_findings()
        for severity, readout in self.counts.items():
            readout.set(str(sum(f.severity == severity for f in findings)))
        self.last_scan.set(QDateTime.currentDateTime().toString("HH:mm"))
        self.table.setRowCount(len(findings))
        for row, finding in enumerate(findings):
            cells = (
                turkish_upper(finding.severity),
                CATEGORY_NAMES.get(finding.category, finding.category),
                finding.target,
                finding.message,
                "Otomatik" if finding.fix is not None else "Elle",
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if column == 0:
                    item.setForeground(qcolor(SEVERITY_COLORS.get(finding.severity, TEXT)))
                if column == 4 and finding.fix is not None:
                    item.setForeground(qcolor(ORANGE))
                self.table.setItem(row, column, item)
        if findings:
            self.table.selectRow(0)
        self.show_details()
        fixable = self.fixable_count()
        self.close_button.setEnabled(fixable > 0)
        if not findings:
            text = "Sorun bulunmadı."
        elif fixable:
            text = f"{len(findings)} bulgu; {fixable} tanesi \"Açıkları Kapat\" ile düzeltilebilir."
        else:
            text = f"{len(findings)} bulgu; otomatik düzeltilebilecek bir şey yok, öneriler tabloda."
        if not report.as_root:
            text += " Tarama kullanıcı yetkisiyle yapıldı; bazı portların sahibi görünmeyebilir."
        set_status(self.status, text, True)

    def show_details(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not getattr(self, "findings", None):
            self.details.setText("Ayrıntılar için tablodan bir bulgu seçin." if self.table.rowCount()
                                 else "")
            return
        finding = self.findings[rows[0].row()]
        fix = "otomatik düzeltilebilir" if finding.fix is not None else "elle yapılmalı"
        # The target is in the table (and in full in the command for file findings).
        self.details.setText(f"{finding.message}\nNe yapılmalı ({fix}): {finding.suggestion}")

    def fixable_count(self) -> int:
        if self.report is None:
            return 0
        return sum(f.fix is not None for f in self.report.findings)

    def confirm_close(self, count: int) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Açıkları Kapat")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"{count} bulgu için güvenli düzeltmeler root olarak uygulanacak.")
        box.setInformativeText("Yalnızca dosya izinleri değişir; her değişiklik "
                               "/var/log/perdix/kilavuz.log dosyasına yazılır. Devam edilsin mi?")
        apply = box.addButton("Uygula", QMessageBox.AcceptRole)
        box.addButton("Vazgeç", QMessageBox.RejectRole)
        box.exec_()
        return box.clickedButton() is apply

    def close_clicked(self) -> None:
        if self.busy() or not self.confirm_close(self.fixable_count()):
            return
        self._start(kilavuz.close_vulnerabilities, self.closed,
                    "Açıklar kapatılıyor: klasörler root olarak yeniden taranıyor…")

    def closed(self, result) -> None:
        if isinstance(result, Exception):
            ok, outcome = False, str(result)
        else:
            ok, outcome = result
        self.scan_button.setEnabled(True)
        self.close_button.setEnabled(self.fixable_count() > 0)
        if not ok:
            set_status(self.status, f"Açıklar kapatılamadı: {outcome}", False)
            return
        message = kilavuz.summarize(outcome)
        failed = any(r.get("status") == "hata" for r in outcome)
        # Re-scan so the table shows what is left; keep the outcome on screen afterwards.
        self._start(kilavuz.scan, lambda report: self._after_close(report, message, not failed),
                    message + " Yeniden taranıyor…")

    def _after_close(self, report, message: str, ok: bool) -> None:
        self.show_report(report)
        if not isinstance(report, Exception):
            set_status(self.status, f"{message} {self.status.text()}", ok)


# --- Main window ----------------------------------------------------------------------------------


class HubWindow(QWidget):
    # (number, icon, title, subtitle) per menu entry, derived from NAV.
    MODULES = [(f"{i + 1:02d}", icon, title, subtitle) for i, (_k, icon, title, subtitle, _g) in enumerate(NAV)]
    PAGES = {"sens": "SensPage", "scope": "ScopePage", "macro": "MacroPage", "hud": "HudPage",
             "vision": "VisionPage", "network": "NetworkPage", "wifi": "WifiPage", "audio": "AudioPage",
             "security": "SecurityPage", "taskman": "TaskManagerPage", "devices": "DevicesPage",
             "diagnostics": "DiagnosticsPage", "region": "RegionPage",
             "bluetooth": "BluetoothPage"}

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
        self.sidebar = Sidebar(NAV)
        middle.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.pages.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        for key, *_rest in NAV:
            page = globals()[self.PAGES[key]]()
            setattr(self, key, page)  # self.sens, self.vision, ...
            holder = QWidget()
            holder_layout = QVBoxLayout(holder)
            holder_layout.setContentsMargins(32, 26, 32, 26)
            holder_layout.addWidget(page)
            # A page taller than a small window scrolls instead of squeezing its controls together.
            scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(holder)
            self.pages.addWidget(scroll)
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
        self.wifi.shutdown()
        self.audio.shutdown()
        self.vision.shutdown()
        self.taskman.shutdown()
        self.devices.shutdown()
        self.diagnostics.shutdown()
        self.region.shutdown()
        self.bluetooth.shutdown()
        if self.security.busy():
            self.security._worker.wait()  # never leave a scan or perdix-tune half-way
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


def apply_saved_audio_effects() -> int:
    """Start EasyEffects with the saved effects at login (nothing to do when they are all off)."""
    effects = audio.Effects.from_dict(hub_settings.load().get("audio"))
    if effects.any and audio.easyeffects_installed():
        try:
            print(audio.apply_effects(effects))
        except audio.AudioError as error:
            print(f"Sound effects: {error}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PERDIX Control Center")
    parser.add_argument("--maximized", action="store_true", help="open maximised (kiosk desktop)")
    parser.add_argument("--apply-saved", action="store_true",
                        help="re-apply saved mouse settings and sound effects, then exit (session start)")
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if args.apply_saved:
        apply_saved_mouse_settings()
        return apply_saved_audio_effects()

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("PERDIX Control Center")
    app.setStyleSheet(STYLESHEET)
    window = HubWindow()
    window.showMaximized() if args.maximized else window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
