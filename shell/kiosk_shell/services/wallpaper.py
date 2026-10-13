"""Desktop wallpapers.

The PERDIX defaults (grey partridges, Perdix perdix, in the mountains) live in
/usr/share/backgrounds/perdix; pictures the user drops into ~/Pictures/Wallpapers join them.
By default the desktop shows them in turn, a new one every 30 minutes. Settings are the
"wallpaper" section of ~/.config/perdix-os/settings.json:

  {"mode": "rotate" | "fixed" | "off", "file": "<path for fixed>", "minutes": 30}
"""

from __future__ import annotations

import os

from kiosk_shell.services import hub_settings

SYSTEM_DIR = "/usr/share/backgrounds/perdix"
# Running from the repository (development, tests): the copy in rootfs/.
REPO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "rootfs", "usr", "share",
                        "backgrounds", "perdix")
USER_DIR = os.path.expanduser("~/Pictures/Wallpapers")
EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
DEFAULTS = {"mode": "rotate", "file": "", "minutes": 30}


def _images(folder: str) -> list[str]:
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names if n.lower().endswith(EXTENSIONS)]


def available(system_dir: str = SYSTEM_DIR, user_dir: str = USER_DIR) -> list[str]:
    defaults = _images(system_dir) or _images(os.path.normpath(REPO_DIR))
    return defaults + _images(user_dir)


def settings() -> dict:
    values = hub_settings.merged(DEFAULTS, hub_settings.load().get("wallpaper"))
    if values["mode"] not in ("rotate", "fixed", "off"):
        values["mode"] = "rotate"
    values["minutes"] = max(1, min(24 * 60, values["minutes"]))
    return values


def playlist(values: dict | None = None, images: list[str] | None = None) -> list[str]:
    """The wallpapers to show, in order: none when off, one when fixed (if it still exists)."""
    values = values or settings()
    images = available() if images is None else images
    if values["mode"] == "off":
        return []
    if values["mode"] == "fixed":
        if values["file"] and os.path.isfile(values["file"]):
            return [values["file"]]
        return images[:1]
    return images
