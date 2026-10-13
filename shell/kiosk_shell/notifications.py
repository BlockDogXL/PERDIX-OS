"""PERDIX notifications: the desktop's own notification server, with neon-orange toasts.

Implements org.freedesktop.Notifications (the Desktop Notifications Specification 1.2) on the
session bus inside the desktop shell, so every `notify-send` and every app using libnotify (game
recording, the updater, Steam, Discord, ...) shows a PERDIX toast: top right, dark glass with an
orange glow, never taking keyboard focus from a game. Clicking a toast runs its default action;
buttons run the other actions (ActionInvoked), and every close is reported (NotificationClosed).
"""

from __future__ import annotations

import re

from PyQt5.QtCore import Q_CLASSINFO, QObject, QPoint, QRectF, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusMessage
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from kiosk_shell.services import theme

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
DEFAULT_TIMEOUT_MS = 6000
WIDTH, MARGIN, GAP = 380, 24, 12
# NotificationClosed reasons
EXPIRED, DISMISSED, CLOSED_BY_CALL = 1, 2, 3

INTROSPECTION = f"""
<interface name="{SERVICE}">
  <method name="GetCapabilities"><arg direction="out" type="as"/></method>
  <method name="Notify">
    <arg direction="in" type="s" name="app_name"/><arg direction="in" type="u" name="replaces_id"/>
    <arg direction="in" type="s" name="app_icon"/><arg direction="in" type="s" name="summary"/>
    <arg direction="in" type="s" name="body"/><arg direction="in" type="as" name="actions"/>
    <arg direction="in" type="a{{sv}}" name="hints"/><arg direction="in" type="i" name="expire_timeout"/>
    <arg direction="out" type="u"/>
  </method>
  <method name="CloseNotification"><arg direction="in" type="u" name="id"/></method>
  <method name="GetServerInformation">
    <arg direction="out" type="s"/><arg direction="out" type="s"/>
    <arg direction="out" type="s"/><arg direction="out" type="s"/>
  </method>
  <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
  <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
</interface>
"""


def plain(text: str) -> str:
    """Bodies may carry a little markup; keep the text only (and never let it run as rich text)."""
    text = re.sub(r"<[^>]*>", "", text or "")
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace(
        "&amp;", "&")


class Toast(QWidget):
    """One notification on screen."""

    clicked = pyqtSignal(str)  # action key ("default" for the toast itself)
    closed = pyqtSignal(int)  # reason

    def __init__(self, app_name: str, summary: str, body: str, actions: list[tuple[str, str]], critical: bool,
                 accent: str) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.X11BypassWindowManagerHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.PointingHandCursor)
        self.accent = QColor("#ff3355" if critical else accent)
        self.actions = dict(actions)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 16, 18, 16)
        layout.setSpacing(4)
        head = QLabel((app_name or "PERDIX").upper())
        font = QFont(head.font())
        font.setPixelSize(10)
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        head.setFont(font)
        head.setStyleSheet(f"color: {self.accent.name()};")
        self.title = QLabel(plain(summary))
        self.title.setTextFormat(Qt.PlainText)
        self.title.setWordWrap(True)
        self.title.setStyleSheet("color: #ffffff; font-size: 15px; font-weight: 700;")
        self.body = QLabel(plain(body))
        self.body.setTextFormat(Qt.PlainText)
        self.body.setWordWrap(True)
        self.body.setStyleSheet("color: #c9d3dc; font-size: 13px;")
        self.body.setVisible(bool(body))
        for w in (head, self.title, self.body):
            layout.addWidget(w)
        buttons = [(key, label) for key, label in actions if key != "default"]
        if buttons:
            row = QHBoxLayout()
            row.setContentsMargins(0, 6, 0, 0)
            for key, label in buttons:
                b = QPushButton(label, cursor=Qt.PointingHandCursor)
                b.setFocusPolicy(Qt.NoFocus)
                b.setStyleSheet(f"QPushButton {{ color: {self.accent.name()}; background: transparent; border: 1px "
                                f"solid {self.accent.name()}; border-radius: 4px; padding: 4px 12px; font-weight: 600; }}"
                                f"QPushButton:hover {{ background: {self.accent.name()}; color: #0a0b0d; }}")
                b.clicked.connect(lambda _c=False, k=key: self.clicked.emit(k))
                row.addWidget(b)
            row.addStretch(1)
            layout.addLayout(row)
        self.setFixedWidth(WIDTH)
        self.adjustSize()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        for spread, alpha in ((6, 0.06), (4, 0.10), (2, 0.18)):  # neon glow
            glow = QColor(self.accent)
            glow.setAlphaF(alpha)
            p.setPen(QPen(glow, spread * 2))
            p.drawRoundedRect(r, 8, 8)
        path = QPainterPath()
        path.addRoundedRect(r, 8, 8)
        p.fillPath(path, QColor(10, 11, 13, 240))
        p.setPen(QPen(self.accent, 1.5))
        p.drawRoundedRect(r, 8, 8)
        p.fillRect(QRectF(r.left() + 1, r.top() + 14, 3, r.height() - 28), self.accent)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit("default")
        else:
            self.closed.emit(DISMISSED)


class _Adaptor(QDBusAbstractAdaptor):
    Q_CLASSINFO("D-Bus Interface", SERVICE)
    Q_CLASSINFO("D-Bus Introspection", INTROSPECTION)
    NotificationClosed = pyqtSignal("uint", "uint")
    ActionInvoked = pyqtSignal("uint", str)

    def __init__(self, server: "NotificationServer") -> None:
        super().__init__(server)
        self.server = server
        self.setAutoRelaySignals(True)

    @pyqtSlot(result="QStringList")
    def GetCapabilities(self):
        return ["actions", "body", "persistence"]

    @pyqtSlot(str, "uint", str, str, str, "QStringList", "QVariantMap", int, result="uint")
    def Notify(self, app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout):
        return self.server.notify(app_name, replaces_id, summary, body, list(actions), dict(hints), expire_timeout)

    @pyqtSlot("uint")
    def CloseNotification(self, notification_id):
        self.server.close(notification_id, CLOSED_BY_CALL)

    @pyqtSlot(QDBusMessage)
    def GetServerInformation(self, message):
        """Four out-arguments (name, vendor, version, spec version): answered by hand."""
        message.setDelayedReply(True)
        QDBusConnection.sessionBus().send(message.createReply(["PERDIX Notifications", "PERDIX OS", "1.0", "1.2"]))


class NotificationServer(QObject):
    NotificationClosed = pyqtSignal("uint", "uint")
    ActionInvoked = pyqtSignal("uint", str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.toasts: dict[int, Toast] = {}
        self.next_id = 1
        self.adaptor = _Adaptor(self)

    def register(self, bus: QDBusConnection | None = None) -> bool:
        """Take the notification service name; False when there is no session bus or another
        notification server already runs."""
        bus = bus or QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        if not bus.registerObject(PATH, self):
            return False
        return bus.registerService(SERVICE)

    def notify(self, app_name: str, replaces_id: int, summary: str, body: str, actions: list[str], hints: dict,
               expire_timeout: int) -> int:
        notification_id = replaces_id if replaces_id in self.toasts else self.next_id
        if notification_id == self.next_id:
            self.next_id += 1
        else:
            self.toasts.pop(notification_id).deleteLater()
        pairs = [(actions[i], actions[i + 1]) for i in range(0, len(actions) - 1, 2)]
        urgency = hints.get("urgency", 1)
        critical = int(urgency) == 2 if isinstance(urgency, (int, float)) else False
        toast = Toast(app_name, summary, body, pairs, critical, theme.load()["orange"])
        toast.clicked.connect(lambda key, n=notification_id: self.invoke(n, key))
        toast.closed.connect(lambda reason, n=notification_id: self.close(n, reason))
        self.toasts[notification_id] = toast
        timeout = DEFAULT_TIMEOUT_MS if expire_timeout < 0 else expire_timeout
        if timeout and not critical:
            QTimer.singleShot(timeout, lambda n=notification_id: self.close(n, EXPIRED))
        self.place()
        toast.show()
        return notification_id

    def invoke(self, notification_id: int, key: str) -> None:
        toast = self.toasts.get(notification_id)
        if toast is None:
            return
        if key in toast.actions:  # a click on a toast without a "default" action only closes it
            self.ActionInvoked.emit(notification_id, key)
        self.close(notification_id, DISMISSED)

    def close(self, notification_id: int, reason: int) -> None:
        toast = self.toasts.pop(notification_id, None)
        if toast is None:
            return
        toast.close()
        self.NotificationClosed.emit(notification_id, reason)
        self.place()

    def place(self) -> None:
        """Newest at the top right, older ones below it."""
        screen = QApplication.primaryScreen().availableGeometry()
        y = screen.top() + MARGIN
        for notification_id in sorted(self.toasts, reverse=True):
            toast = self.toasts[notification_id]
            toast.move(QPoint(screen.right() - WIDTH - MARGIN + 1, y))
            y += toast.height() + GAP


def start(parent=None) -> NotificationServer | None:
    server = NotificationServer(parent)
    return server if server.register() else None

