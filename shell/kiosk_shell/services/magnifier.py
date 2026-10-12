"""Zoom the centre of the screen while a key is held (Tactical Scope's zoom key).

Backends, picked automatically:
  X11 (PERDIX's session): `xrandr --transform` scales the middle of the output up to fill
      the screen, so it works over any game without screen capture or a compositor.
  Hyprland (if PERDIX ever runs on it): `hyprctl keyword cursor:zoom_factor`. Hyprland zooms
      around the cursor, which shooters keep locked in the middle of the screen.
Hyprland has no D-Bus magnifier interface; GNOME's magnifier does, but PERDIX doesn't use GNOME.
"""

import os
import re
import shutil
import subprocess

FACTORS = (1.5, 2.0, 2.5, 3.0)


def zoom_transform(width: int, height: int, factor: float) -> str:
    """xrandr transform matrix that shows the centre 1/factor of the screen at full size."""
    scale = 1.0 / factor
    dx = width * (1 - scale) / 2
    dy = height * (1 - scale) / 2
    return f"{scale:.6f},0,{dx:.3f},0,{scale:.6f},{dy:.3f},0,0,1"


_OUTPUT = re.compile(r"^(?P<name>\S+) connected (?P<primary>primary )?(?P<w>\d+)x(?P<h>\d+)\+\d+\+\d+")


def parse_xrandr(text: str) -> tuple[str, int, int] | None:
    """(output, width, height) of the primary output, or the first active one."""
    found = None
    for line in text.splitlines():
        match = _OUTPUT.match(line)
        if match:
            output = (match["name"], int(match["w"]), int(match["h"]))
            if match["primary"]:
                return output
            found = found or output
    return found


class XrandrMagnifier:
    name = "X11 (xrandr transform)"

    def __init__(self) -> None:
        self._output: tuple[str, int, int] | None = None

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY") and bool(shutil.which("xrandr"))

    def _target(self) -> tuple[str, int, int] | None:
        if self._output is None:
            text = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=3).stdout
            self._output = parse_xrandr(text)
        return self._output

    def zoom(self, factor: float) -> bool:
        target = self._target()
        if target is None:
            return False
        name, width, height = target
        return subprocess.run(["xrandr", "--output", name, "--filter", "bilinear",
                               "--transform", zoom_transform(width, height, factor)],
                              capture_output=True, timeout=3).returncode == 0

    def reset(self) -> bool:
        target = self._target()
        if target is None:
            return False
        return subprocess.run(["xrandr", "--output", target[0], "--transform", "none"],
                              capture_output=True, timeout=3).returncode == 0


class HyprlandMagnifier:
    name = "Hyprland (cursor zoom)"

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and bool(shutil.which("hyprctl"))

    def zoom(self, factor: float) -> bool:
        return subprocess.run(["hyprctl", "keyword", "cursor:zoom_factor", f"{factor:.2f}"],
                              capture_output=True, timeout=3).returncode == 0

    def reset(self) -> bool:
        return subprocess.run(["hyprctl", "keyword", "cursor:zoom_factor", "1"],
                              capture_output=True, timeout=3).returncode == 0


def detect():
    for backend in (HyprlandMagnifier, XrandrMagnifier):
        if backend.available():
            return backend()
    return None
