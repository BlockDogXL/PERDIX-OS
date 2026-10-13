"""Power off / reboot via systemctl (the polkit rule lets the kiosk user do this without a password).

With KIOSK_DEV=1 the commands are only printed, so testing the shell never turns off a dev machine.
"""

import os

from PyQt5.QtCore import QProcess


def _systemctl(action: str) -> bool:
    if os.environ.get("KIOSK_DEV") == "1":
        print(f"KIOSK_DEV: not running 'systemctl {action}'", flush=True)
        return True
    return QProcess.startDetached("systemctl", [action])


def power_off() -> bool:
    return _systemctl("poweroff")


def reboot() -> bool:
    return _systemctl("reboot")
