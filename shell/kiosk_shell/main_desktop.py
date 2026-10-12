"""PERDIX OS desktop: the full-screen home screen the kiosk boots into.

Black and neon-blue interface with Piri, the PERDIX mascot, glowing in the centre and
four actions: Open Games (Steam), My Files, Antivirus Scan and Shut Down System. The
Piri icon in the top-left corner opens the About System page.

Kiosk behaviour: a frameless full-screen window that ignores close requests
(Alt+F4, window-manager close, Ctrl+C) and goes back to full screen if it is
minimised. If the process dies anyway, /usr/local/bin/kiosk-session restarts it.

KIOSK_DEV=1 runs it in a normal 1280x720 window, lets Ctrl+Q quit, and only
prints power actions instead of running them.

Run:  python -m kiosk_shell            (from shell/)
      python kiosk_shell/main_desktop.py
"""

from __future__ import annotations

import math
import os
import signal
import sys

if __package__ in (None, ""):  # run as a plain script: make the kiosk_shell package importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QDateTime, QDir, QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt5.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileSystemModel,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListView,
    QPlainTextEdit,
    QPushButton,
    QShortcut,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from kiosk_shell.config import load_config, read_shell_vars
from kiosk_shell.services import power
from kiosk_shell.services.antivirus import ScanResult, VirusScanner
from kiosk_shell.services import competitor, system_info
from kiosk_shell.services.launcher import AppLauncher

DEV_MODE = os.environ.get("KIOSK_DEV") == "1"

BLACK = QColor("#030508")
NEON = QColor("#00c8ff")
NEON_DEEP = QColor("#0066ff")
NEON_WHITE = QColor("#c8f4ff")
TEXT = QColor("#e6f7ff")
MUTED = QColor("#5d7a8f")
DANGER = QColor("#ff4d6d")
PIRI_BELLY = QColor("#c8f4ff")  # Piri's light belly; its feather lines are NEON

BRAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "brand")

# Everything is sized for 1080p and scaled to the actual screen height in main().
_scale = 1.0


def px(value: float) -> int:
    return max(1, round(value * _scale))


def build_stylesheet() -> str:
    return f"""
    QWidget {{
        color: {TEXT.name()};
        font-family: "Inter", "Noto Sans", "DejaVu Sans", sans-serif;
        font-size: {px(20)}px;
    }}
    QLabel#PageTitle {{ font-size: {px(44)}px; font-weight: 600; color: {NEON.name()}; }}
    QLabel#Muted {{ color: {MUTED.name()}; }}
    QLabel#Clock {{ font-size: {px(34)}px; font-weight: 300; }}
    QLabel#Status {{ font-size: {px(30)}px; font-weight: 300; }}
    QPushButton#Flat {{
        background: transparent;
        border: {px(2)}px solid rgba(0, 200, 255, 90);
        border-radius: {px(12)}px;
        padding: {px(12)}px {px(28)}px;
    }}
    QPushButton#Flat:hover, QPushButton#Flat:focus {{
        border-color: {NEON.name()};
        background: rgba(0, 200, 255, 35);
    }}
    QPushButton#Flat:disabled {{ color: {MUTED.name()}; border-color: rgba(93, 122, 143, 60); }}
    QPushButton#Danger {{
        background: rgba(255, 77, 109, 30);
        border: {px(2)}px solid {DANGER.name()};
        border-radius: {px(12)}px;
        padding: {px(12)}px {px(28)}px;
    }}
    QPushButton#Danger:hover, QPushButton#Danger:focus {{ background: rgba(255, 77, 109, 80); }}
    QFrame#Dialog {{
        background: rgba(8, 14, 22, 245);
        border: {px(2)}px solid {NEON.name()};
        border-radius: {px(24)}px;
    }}
    QListView, QPlainTextEdit {{
        background: rgba(8, 14, 22, 220);
        border: {px(1)}px solid rgba(0, 200, 255, 70);
        border-radius: {px(16)}px;
        padding: {px(10)}px;
        outline: none;
        selection-background-color: rgba(0, 200, 255, 60);
    }}
    QListView::item {{ padding: {px(10)}px; border-radius: {px(8)}px; }}
    QListView::item:selected {{ background: rgba(0, 200, 255, 50); color: {TEXT.name()}; }}
    QScrollBar:vertical {{ background: transparent; width: {px(10)}px; margin: {px(4)}px; }}
    QScrollBar::handle:vertical {{
        background: rgba(0, 200, 255, 100); border-radius: {px(3)}px; min-height: {px(40)}px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    """


# --- drawing helpers ---------------------------------------------------------------------------


def with_alpha(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def stroke_neon(painter: QPainter, path: QPainterPath, color: QColor, width: float, glow: float = 1.0) -> None:
    """Stroke a path as a neon tube: wide faint passes for the glow, then the bright core."""
    for spread, alpha in ((7.0, 0.05), (4.0, 0.10), (2.2, 0.22)):
        pen = QPen(with_alpha(color, alpha * glow), width * spread, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.strokePath(path, pen)
    painter.strokePath(path, QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.strokePath(path, QPen(with_alpha(NEON_WHITE, 0.8 * glow), width * 0.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))


def load_mask(name: str) -> QImage:
    """White-on-transparent Piri mask from resources/brand (made by tools/make-brand-assets.py).
    A missing file gives a null image; callers then just skip the logo."""
    return QImage(os.path.join(BRAND_DIR, name)).convertToFormat(QImage.Format_ARGB32_Premultiplied)


def tinted(mask: QImage, color: QColor, size: int) -> QImage:
    """The mask scaled to fit size x size and filled with one colour."""
    image = mask.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    painter = QPainter(image)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(image.rect(), color)
    painter.end()
    return image


def glow_image(mask: QImage, color: QColor, size: int, softness: int) -> QImage:
    """A soft glow around the mask: draw it on a padded canvas, then blur by halving and doubling."""
    pad = size // 4
    canvas = QImage(size + 2 * pad, size + 2 * pad, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawImage(pad, pad, tinted(mask, color, size))
    painter.end()
    small, steps = canvas, 0
    while small.width() > canvas.width() / softness and small.width() > 8:
        small = small.scaled(small.width() // 2, small.height() // 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        steps += 1
    for _ in range(steps):
        small = small.scaled(small.width() * 2, small.height() * 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return small.scaled(canvas.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def icon_path(kind: str) -> QPainterPath:
    """Line icons in a 100x100 box centred on (0, 0)."""
    p = QPainterPath()
    if kind == "games":  # gamepad
        p.addRoundedRect(QRectF(-46, -24, 92, 50), 25, 25)
        p.moveTo(-31, 1); p.lineTo(-13, 1)
        p.moveTo(-22, -8); p.lineTo(-22, 10)
        p.addEllipse(QPointF(17, -5), 4, 4)
        p.addEllipse(QPointF(29, 7), 4, 4)
    elif kind == "files":  # folder
        p.moveTo(-44, -30); p.lineTo(-14, -30); p.lineTo(-6, -20); p.lineTo(44, -20)
        p.lineTo(44, 32); p.lineTo(-44, 32); p.closeSubpath()
        p.moveTo(-44, -8); p.lineTo(44, -8)
    elif kind == "scan":  # shield with a check mark
        p.moveTo(0, -44); p.lineTo(38, -30); p.lineTo(38, 0)
        p.cubicTo(38, 24, 18, 38, 0, 46)
        p.cubicTo(-18, 38, -38, 24, -38, 0)
        p.lineTo(-38, -30); p.closeSubpath()
        p.moveTo(-16, 2); p.lineTo(-4, 14); p.lineTo(18, -12)
    elif kind == "power":  # power symbol
        rect = QRectF(-36, -30, 72, 72)
        p.arcMoveTo(rect, 120)
        p.arcTo(rect, 120, 300)
        p.moveTo(0, -46); p.lineTo(0, -4)
    return p


# --- widgets -------------------------------------------------------------------------------------


class Backdrop(QWidget):
    """Black background with a faint blue grid and a glow behind the logo."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), BLACK)
        center = QPointF(self.width() / 2, self.height() * 0.42)
        glow = QRadialGradient(center, max(self.width(), self.height()) * 0.55)
        glow.setColorAt(0.0, with_alpha(NEON_DEEP, 0.22))
        glow.setColorAt(0.5, with_alpha(NEON_DEEP, 0.06))
        glow.setColorAt(1.0, with_alpha(NEON_DEEP, 0.0))
        painter.fillRect(self.rect(), glow)
        painter.setPen(QPen(with_alpha(NEON, 0.035), 1))
        step = px(64)
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)


class PiriLogo(QWidget):
    """Piri, the PERDIX mascot, in neon blue with a slowly pulsing glow and a turning ring.

    The layers are rendered once per size; each frame only blends finished images. The
    animation runs only while the widget is visible and animation is enabled.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(px(260), px(260))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._strokes = load_mask("piri-strokes-mask.png")
        self._silhouette = load_mask("piri-silhouette-mask.png")
        self._rendered_size = 0
        self._phase = 0.0
        self._animated = True
        self._timer = QTimer(self, interval=50, timeout=self._tick)

    def set_animated(self, animated: bool) -> None:
        """Stop animating while a game runs so the shell uses no CPU in the background."""
        self._animated = animated
        if animated and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._animated:
            self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.05) % (2 * math.pi * 10)
        self.update()

    def _render(self, size: int) -> None:
        self._rendered_size = size
        self._glow_wide = glow_image(self._silhouette, NEON_DEEP, size, 14)
        self._glow_tight = glow_image(self._silhouette, NEON, size, 40)
        self._art = tinted(self._silhouette, PIRI_BELLY, size)
        painter = QPainter(self._art)
        painter.drawImage(0, 0, tinted(self._strokes, NEON, size))
        painter.end()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        glow = 0.75 + 0.25 * math.sin(self._phase * 1.6)
        center = QPointF(self.width() / 2, self.height() / 2)
        r = min(self.width(), self.height()) * 0.46

        halo = QRadialGradient(center, r * 1.15)
        halo.setColorAt(0.0, with_alpha(NEON_DEEP, 0.28 * glow))
        halo.setColorAt(1.0, with_alpha(NEON_DEEP, 0.0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, r * 1.15, r * 1.15)

        # Slowly turning dashed ring around Piri.
        painter.save()
        painter.translate(center)
        painter.rotate(math.degrees(self._phase * 0.15))
        ring = QPen(with_alpha(NEON, 0.45), max(1.0, r * 0.012))
        ring.setDashPattern([2, 6])
        painter.setPen(ring)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0, 0), r, r)
        painter.restore()

        if self._strokes.isNull() or self._silhouette.isNull():
            return  # brand resources missing: show the ring only rather than crash
        size = int(r * 1.45)
        if size != self._rendered_size:
            self._render(size)

        def draw_centred(image: QImage, opacity: float) -> None:
            painter.setOpacity(opacity)
            painter.drawImage(QPointF(center.x() - image.width() / 2, center.y() - image.height() / 2), image)

        draw_centred(self._glow_wide, 0.9 * glow)
        draw_centred(self._glow_tight, 0.7 * glow)
        draw_centred(self._art, 1.0)
        painter.setOpacity(1.0)


class BrandButton(QPushButton):
    """Top-left Piri icon and wordmark. Opens About System, like a start-menu button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mask = load_mask("piri-silhouette-mask.png")
        self._icons: dict[str, QImage] = {}
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("About this system")
        self.setFixedSize(px(230), px(64))

    def _icon(self, color: QColor) -> QImage:
        if color.name() not in self._icons:
            self._icons[color.name()] = tinted(self._mask, color, px(40))
        return self._icons[color.name()]

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.animateClick()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        lit = self.underMouse() or self.hasFocus()
        if lit:
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), px(14), px(14))
            painter.fillPath(shape, with_alpha(NEON, 0.10))
            painter.strokePath(shape, QPen(with_alpha(NEON, 0.8), px(2)))
        color = NEON if lit else TEXT
        x = px(12)
        if not self._mask.isNull():
            icon = self._icon(color)
            painter.drawImage(x, (self.height() - icon.height()) // 2, icon)
            x += icon.width() + px(14)
        font = QFont(self.font())
        font.setPixelSize(px(20))
        font.setWeight(QFont.DemiBold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, px(7))
        painter.setFont(font)
        painter.setPen(color if lit else MUTED)
        painter.drawText(QRectF(x, 0, self.width() - x, self.height()), Qt.AlignLeft | Qt.AlignVCenter, "PERDIX")


class NeonButton(QPushButton):
    """Large tile with a line icon and a glowing border that lights up on hover/focus."""

    def __init__(self, text: str, icon: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._icon = icon_path(icon)
        self._badge = ""
        self._highlight = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(px(290), px(200))
        self._anim = QVariantAnimation(self, duration=180)
        self._anim.valueChanged.connect(self._set_highlight)

    def set_badge(self, text: str) -> None:
        self._badge = text
        self.update()

    def _set_highlight(self, value: float) -> None:
        self._highlight = value
        self.update()

    def _animate(self) -> None:
        target = 1.0 if (self.underMouse() or self.hasFocus()) and self.isEnabled() else 0.0
        self._anim.stop()
        self._anim.setStartValue(self._highlight)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._animate()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._animate()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._animate()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._animate()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.animateClick()
        elif key in (Qt.Key_Right, Qt.Key_Down):
            self.focusNextChild()
        elif key in (Qt.Key_Left, Qt.Key_Up):
            self.focusPreviousChild()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        h = self._highlight
        inset = px(14)
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        if self.isDown():
            rect.adjust(px(3), px(3), -px(3), -px(3))
        shape = QPainterPath()
        shape.addRoundedRect(rect, px(22), px(22))

        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill.setColorAt(0.0, QColor(14, 26, 40))
        fill.setColorAt(1.0, QColor(5, 9, 14))
        painter.fillPath(shape, fill)
        painter.fillPath(shape, with_alpha(NEON, 0.10 * h))

        if h > 0.01:
            for spread, alpha in ((10, 0.05), (6, 0.10), (3, 0.20)):
                painter.strokePath(shape, QPen(with_alpha(NEON, alpha * h), px(spread)))
        painter.strokePath(shape, QPen(with_alpha(NEON, 0.35 + 0.65 * h), px(2)))

        icon_color = NEON if self.isEnabled() else MUTED
        size = rect.height() * 0.38
        painter.save()
        painter.translate(rect.center().x(), rect.top() + rect.height() * 0.40)
        painter.scale(size / 100, size / 100)
        stroke_neon(painter, self._icon, icon_color, 6, 0.4 + 0.6 * h)
        painter.restore()

        font = QFont(self.font())
        font.setPixelSize(px(22))
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(TEXT if self.isEnabled() else MUTED)
        label = QRectF(rect.left(), rect.top() + rect.height() * 0.68, rect.width(), rect.height() * 0.24)
        painter.drawText(label, Qt.AlignCenter, self.text())

        if self._badge:
            font.setPixelSize(px(13))
            font.setLetterSpacing(QFont.AbsoluteSpacing, px(2))
            painter.setFont(font)
            painter.setPen(NEON)
            badge = QRectF(rect.left(), rect.top() + px(10), rect.width() - px(16), px(20))
            painter.drawText(badge, Qt.AlignRight | Qt.AlignVCenter, self._badge)


def glow(widget: QWidget, radius: int = 40, color: QColor = NEON) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(px(radius))
    effect.setColor(color)
    effect.setOffset(0, 0)
    widget.setGraphicsEffect(effect)


def spaced_label(text: str, size: int, spacing: int, weight: int = QFont.Normal, name: str = "") -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    # Size and weight must go through a stylesheet (the app stylesheet would override setFont);
    # stylesheets have no letter spacing, so that stays on the font.
    css_weight = {QFont.Light: 300, QFont.Normal: 400, QFont.DemiBold: 600}.get(weight, 400)
    label.setStyleSheet(f"font-size: {px(size)}px; font-weight: {css_weight};")
    font = QFont(label.font())
    font.setLetterSpacing(QFont.AbsoluteSpacing, px(spacing))
    label.setFont(font)
    label.setAlignment(Qt.AlignCenter)
    return label


def flat_button(text: str, name: str = "Flat") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(name)
    button.setCursor(Qt.PointingHandCursor)
    return button


def page_header(title: str, back: QPushButton, *extra: QWidget) -> QHBoxLayout:
    header = QHBoxLayout()
    label = QLabel(title)
    label.setObjectName("PageTitle")
    glow(label, 30)
    header.addWidget(label)
    header.addStretch(1)
    for widget in extra:
        header.addWidget(widget)
    header.addWidget(back)
    return header


# --- pages ---------------------------------------------------------------------------------------


class HomePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(px(56), px(36), px(56), px(36))
        layout.setSpacing(0)

        top = QHBoxLayout()
        self.brand_button = BrandButton()
        top.addWidget(self.brand_button, 0, Qt.AlignLeft | Qt.AlignTop)
        top.addStretch(1)
        clock_box = QVBoxLayout()
        clock_box.setSpacing(0)
        self.clock = QLabel(objectName="Clock", alignment=Qt.AlignRight)
        self.date = QLabel(objectName="Muted", alignment=Qt.AlignRight)
        clock_box.addWidget(self.clock)
        clock_box.addWidget(self.date)
        top.addLayout(clock_box)
        layout.addLayout(top)

        self.logo = PiriLogo()
        layout.addWidget(self.logo, 6)
        wordmark = spaced_label("PERDIX", 76, 26, QFont.Light)
        glow(wordmark, 50)
        layout.addWidget(wordmark)
        layout.addSpacing(px(6))
        layout.addWidget(spaced_label("OPERATING  SYSTEM", 16, 10, QFont.Normal, "Muted"))
        layout.addStretch(1)

        self.steam_button = NeonButton("Open Games (Steam)", "games")
        self.files_button = NeonButton("My Files", "files")
        self.scan_button = NeonButton("Antivirus Scan", "scan")
        self.power_button = NeonButton("Shut Down System", "power")
        row = QHBoxLayout()
        row.setSpacing(px(28))
        row.addStretch(1)
        for button in (self.steam_button, self.files_button, self.scan_button, self.power_button):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self.status = QLabel(objectName="Muted", alignment=Qt.AlignCenter)
        self.status.setMinimumHeight(px(40))
        layout.addSpacing(px(12))
        layout.addWidget(self.status)

        self._status_timer = QTimer(self, singleShot=True, timeout=lambda: self.status.setText(""))
        self._clock_timer = QTimer(self, interval=1000, timeout=self._update_clock)
        self._clock_timer.start()
        self._update_clock()

    def _update_clock(self) -> None:
        now = QDateTime.currentDateTime()
        self.clock.setText(now.toString("HH:mm"))
        self.date.setText(now.toString("dddd, d MMMM"))

    def show_message(self, text: str, error: bool = False, seconds: int = 8) -> None:
        self.status.setStyleSheet(f"color: {(DANGER if error else NEON).name()};")
        self.status.setText(text)
        self._status_timer.start(seconds * 1000)


class FilesPage(QWidget):
    """Read-only browser for the kiosk user's home folder. Never leaves it, even through symlinks."""

    def __init__(self, on_back, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = os.path.realpath(QDir.homePath())
        self._on_back = on_back

        layout = QVBoxLayout(self)
        layout.setContentsMargins(px(56), px(40), px(56), px(40))
        layout.setSpacing(px(18))
        self.up_button = flat_button("Up")
        back = flat_button("Back to Desktop")
        layout.addLayout(page_header("My Files", back, self.up_button))
        self.path_label = QLabel(objectName="Muted")
        layout.addWidget(self.path_label)

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot)  # hidden files stay hidden
        self.model.setReadOnly(True)
        self.model.setRootPath(self.root)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setUniformItemSizes(True)
        self.view.activated.connect(self._open)
        self.view.selectionModel().currentChanged.connect(lambda index, _prev: self._describe(index))
        layout.addWidget(self.view, 1)
        self.info = QLabel(objectName="Muted")
        layout.addWidget(self.info)

        self.up_button.clicked.connect(self.go_up)
        back.clicked.connect(on_back)
        self.current = self.root
        self.set_dir(self.root)

    def _inside_home(self, path: str) -> bool:
        real = os.path.realpath(path)
        return real == self.root or real.startswith(self.root + os.sep)

    def set_dir(self, path: str) -> None:
        if not self._inside_home(path):
            self.info.setText("That folder is outside your files.")
            return
        self.current = path
        self.view.setRootIndex(self.model.index(path))
        relative = os.path.relpath(path, self.root)
        self.path_label.setText("Home" if relative == "." else "Home / " + relative.replace(os.sep, " / "))
        self.up_button.setEnabled(os.path.realpath(path) != self.root)
        self.info.setText("")

    def go_up(self) -> None:
        if os.path.realpath(self.current) != self.root:
            self.set_dir(os.path.dirname(self.current))

    def _open(self, index) -> None:
        if self.model.isDir(index):
            self.set_dir(self.model.filePath(index))

    def _describe(self, index) -> None:
        if not index.isValid() or self.model.isDir(index):
            self.info.setText("")
            return
        modified = self.model.lastModified(index).toString("d MMM yyyy, HH:mm")
        self.info.setText(f"{self.model.fileName(index)}  ·  {human_size(self.model.size(index))}  ·  {modified}")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.view.setFocus()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Backspace:
            self.go_up()
        elif event.key() == Qt.Key_Escape:
            self._on_back()
        else:
            super().keyPressEvent(event)


class ScanPage(QWidget):
    def __init__(self, scanner: VirusScanner, on_back, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scanner = scanner
        self._on_back = on_back

        layout = QVBoxLayout(self)
        layout.setContentsMargins(px(56), px(40), px(56), px(40))
        layout.setSpacing(px(18))
        back = flat_button("Back to Desktop")
        back.clicked.connect(on_back)
        layout.addLayout(page_header("Antivirus Scan", back))
        layout.addWidget(QLabel("Checks every file in your home folder with ClamAV.", objectName="Muted"))

        self.status = QLabel("Ready to scan.", objectName="Status")
        layout.addSpacing(px(12))
        layout.addWidget(self.status)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setPlaceholderText("Threats found during the scan are listed here.")
        layout.addWidget(self.log, 1)

        self.action = flat_button("Start Scan")
        self.action.clicked.connect(self._toggle)
        actions = QHBoxLayout()
        actions.addWidget(self.action)
        actions.addStretch(1)
        layout.addLayout(actions)

        scanner.progress.connect(lambda n: self.status.setText(f"Scanning…  {n:,} files checked"))
        scanner.threat_found.connect(lambda path, sig: self.log.appendPlainText(f"THREAT  {sig}\n        {path}"))
        scanner.finished.connect(self._finished)

    def _toggle(self) -> None:
        if self.scanner.is_running():
            self.scanner.cancel()
            return
        self.log.clear()
        self.status.setStyleSheet("")
        self.status.setText("Loading virus definitions…")
        self.action.setText("Cancel Scan")
        self.scanner.start()

    def _finished(self, result: ScanResult) -> None:
        self.action.setText("Try Again" if result.error and not result.files_scanned else "Scan Again")
        if result.error and not result.files_scanned:
            self._set_status(result.error, DANGER)
        elif result.cancelled:
            self._set_status(f"Scan cancelled after {result.files_scanned:,} files.", MUTED)
        elif result.threats:
            self._set_status(f"{len(result.threats)} threat(s) found in {result.files_scanned:,} files.", DANGER)
        else:
            self._set_status(f"No threats found. {result.files_scanned:,} files checked.", NEON)
        if result.error and result.files_scanned:
            self.log.appendPlainText(f"Some files could not be scanned:\n{result.error}")

    def _set_status(self, text: str, color: QColor) -> None:
        self.status.setStyleSheet(f"color: {color.name()};")
        self.status.setText(text)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.action.setFocus()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._on_back()
        else:
            super().keyPressEvent(event)


class AboutPage(QWidget):
    """About System: large Piri with its signature, and the system's version and hardware."""

    ROWS = ("Version", "Edition", "Kernel", "Architecture", "Processor", "Graphics", "Memory", "Desktop")

    def __init__(self, on_back, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_back = on_back
        layout = QVBoxLayout(self)
        layout.setContentsMargins(px(56), px(40), px(56), px(40))
        layout.setSpacing(px(18))
        back = flat_button("Back to Desktop")
        back.clicked.connect(on_back)
        layout.addLayout(page_header("About System", back))

        body = QHBoxLayout()
        body.setSpacing(px(64))
        mascot = QVBoxLayout()
        mascot.setSpacing(px(12))
        self.logo = PiriLogo()
        self.logo.setMinimumSize(px(460), px(460))
        mascot.addWidget(self.logo, 1)
        signature = QLabel("Piri  ·  <i>Perdix perdix</i>", objectName="Muted", alignment=Qt.AlignCenter)
        mascot.addWidget(signature)
        body.addLayout(mascot, 5)

        details = QVBoxLayout()
        details.setSpacing(px(6))  # child layouts otherwise inherit body's 64 px spacing
        details.addStretch(1)
        name = spaced_label("PERDIX", 72, 22, QFont.Light)
        subtitle = spaced_label("OPERATING  SYSTEM", 16, 10, QFont.Normal, "Muted")
        for label in (name, subtitle):
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            details.addWidget(label)
        glow(name, 40)
        details.addSpacing(px(36))
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(36))
        grid.setVerticalSpacing(px(14))
        self.values: dict[str, QLabel] = {}
        for row, title in enumerate(self.ROWS):
            label = QLabel(title, objectName="Muted", alignment=Qt.AlignLeft | Qt.AlignTop)
            value = QLabel(system_info.UNKNOWN, alignment=Qt.AlignLeft | Qt.AlignTop)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.NoTextInteraction)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            self.values[title] = value
        grid.setColumnStretch(1, 1)
        details.addLayout(grid)
        details.addStretch(2)
        body.addLayout(details, 6)
        layout.addLayout(body, 1)

    def refresh(self) -> None:
        info = system_info.collect()
        values = {
            "Version": info.version,
            "Edition": info.edition,
            "Kernel": info.kernel,
            "Architecture": info.architecture,
            "Processor": info.processor,
            "Graphics": "\n".join(info.graphics) or system_info.UNKNOWN,
            "Memory": info.memory,
            "Desktop": f"PERDIX Desktop {info.desktop}",
        }
        for title, text in values.items():
            self.values[title].setText(text)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()  # cheap (reads /proc and runs lspci once), and stays current

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._on_back()
        else:
            super().keyPressEvent(event)


class ShutdownOverlay(QWidget):
    """Dimmed full-window confirmation: Shut Down / Restart / Cancel."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.hide()
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        dialog = QFrame(objectName="Dialog")
        dialog.setFixedWidth(px(720))
        glow(dialog, 60)
        box = QVBoxLayout(dialog)
        box.setContentsMargins(px(48), px(40), px(48), px(40))
        box.setSpacing(px(16))
        self.title = QLabel("Shut down PERDIX OS?", objectName="PageTitle")
        box.addWidget(self.title)
        self.text = QLabel("Progress in running games that has not been saved will be lost.", objectName="Muted")
        self.text.setWordWrap(True)
        box.addWidget(self.text)
        box.addSpacing(px(16))
        buttons = QHBoxLayout()
        self.shutdown = flat_button("Shut Down", "Danger")
        self.restart = flat_button("Restart")
        self.cancel = flat_button("Cancel")
        for button in (self.shutdown, self.restart, self.cancel):
            buttons.addWidget(button)
        box.addLayout(buttons)
        outer.addWidget(dialog)

        self.shutdown.clicked.connect(lambda: self._run(power.power_off, "Shutting down…"))
        self.restart.clicked.connect(lambda: self._run(power.reboot, "Restarting…"))
        self.cancel.clicked.connect(self.hide)

    def open(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        for button in (self.shutdown, self.restart, self.cancel):
            button.setEnabled(True)
        self.title.setText("Shut down PERDIX OS?")
        self.raise_()
        self.show()
        self.cancel.setFocus()  # the safe choice is the default

    def _run(self, action, message: str) -> None:
        for button in (self.shutdown, self.restart, self.cancel):
            button.setEnabled(False)
        self.title.setText(message)
        if not action():
            self.title.setText("Could not reach the power service.")
            self.cancel.setEnabled(True)
            self.cancel.setFocus()
        elif DEV_MODE:
            QTimer.singleShot(1500, self.hide)

    def paintEvent(self, event) -> None:
        QPainter(self).fillRect(self.rect(), with_alpha(BLACK, 0.82))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.cancel.isEnabled():
            self.hide()
        else:
            super().keyPressEvent(event)


# --- main window ---------------------------------------------------------------------------------


class MainDesktop(Backdrop):
    def __init__(self, config: dict[str, str]) -> None:
        super().__init__()
        self.setWindowTitle("PERDIX OS")
        if not DEV_MODE:
            self.setWindowFlags(Qt.FramelessWindowHint)
        self._allow_close = False

        self.steam = AppLauncher(config["KIOSK_STEAM_CMD"], self)
        self.scanner = VirusScanner(QDir.homePath(), self)

        self.home = HomePage()
        self.files = FilesPage(self.show_home)
        self.scan = ScanPage(self.scanner, self.show_home)
        self.about = AboutPage(self.show_home)
        self.pages = QStackedWidget()
        for page in (self.home, self.files, self.scan, self.about):
            self.pages.addWidget(page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.pages)
        self.overlay = ShutdownOverlay(self)

        self.home.steam_button.clicked.connect(self.open_steam)
        self.home.files_button.clicked.connect(lambda: self.pages.setCurrentWidget(self.files))
        self.home.scan_button.clicked.connect(lambda: self.pages.setCurrentWidget(self.scan))
        self.home.power_button.clicked.connect(self.overlay.open)
        self.home.brand_button.clicked.connect(lambda: self.pages.setCurrentWidget(self.about))

        self.steam.started.connect(self._steam_started)
        self.steam.finished.connect(self._steam_finished)
        self.steam.failed.connect(self._steam_failed)
        self.scanner.progress.connect(lambda _n: self.home.scan_button.set_badge("SCANNING"))
        self.scanner.finished.connect(lambda _r: self.home.scan_button.set_badge(""))

        if DEV_MODE:
            QShortcut(QKeySequence("Ctrl+Q"), self, activated=self._quit_dev)
        self.home.steam_button.setFocus()

    def show_home(self) -> None:
        self.pages.setCurrentWidget(self.home)
        self.home.steam_button.setFocus()

    # Steam -------------------------------------------------------------------------------------
    # The shell stays full screen underneath; Openbox stacks the new Steam window on top of it.

    def open_steam(self) -> None:
        if self.steam.is_running():
            self.home.show_message("Steam is already running.")
            return
        self.home.show_message("Starting Steam…", seconds=20)
        # Competitor Mode (PERDIX Control Center) leaves its game environment in a file.
        self.steam.start(read_shell_vars(competitor.ENV_PATH))

    def _steam_started(self) -> None:
        self.home.steam_button.set_badge("RUNNING")
        self.home.logo.set_animated(False)

    def _steam_finished(self, exit_code: int) -> None:
        self.home.steam_button.set_badge("")
        self.home.logo.set_animated(True)
        self._bring_to_front()
        if exit_code != 0:
            self.home.show_message(f"Steam closed with an error (code {exit_code}).", error=True)
        else:
            self.home.show_message("Welcome back.")

    def _steam_failed(self, message: str) -> None:
        self.home.show_message(message, error=True)

    # Kiosk lock-down ---------------------------------------------------------------------------

    def _bring_to_front(self) -> None:
        if DEV_MODE:
            self.showNormal()
        else:
            self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
        else:
            event.ignore()  # Alt+F4 and window-manager close requests do nothing

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (
            not DEV_MODE
            and event.type() == QEvent.WindowStateChange
            and not (self.windowState() & Qt.WindowFullScreen)
        ):
            QTimer.singleShot(0, self.showFullScreen)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.overlay.isVisible():
            self.overlay.setGeometry(self.rect())

    def _quit_dev(self) -> None:
        self._allow_close = True
        QApplication.quit()


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"


def main(argv: list[str] | None = None) -> int:
    global _scale
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("PERDIX OS")
    app.setQuitOnLastWindowClosed(False)
    if not DEV_MODE:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C must not close the kiosk
    screen_height = 720 if DEV_MODE else app.primaryScreen().size().height()
    _scale = max(0.5, screen_height / 1080)
    app.setStyleSheet(build_stylesheet())

    window = MainDesktop(load_config())
    if DEV_MODE:
        window.resize(1280, 720)
        window.show()
    else:
        window.showFullScreen()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
