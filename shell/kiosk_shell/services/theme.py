"""The user's accent colours (~/.config/perdix-os/theme.json), shared by the desktop and the
Control Center. Missing or invalid entries fall back to the PERDIX defaults; the file is
created with the defaults the first time, so it is easy to find and edit.
"""

import json
import os
import re

from kiosk_shell import paths

DEFAULTS = {
    "orange": "#ff6a1a",   # Control Center accent: active menu, buttons, sliders
    "ice": "#8fe3ff",      # Control Center highlight: headings, readouts
    "blue": "#00c8ff",     # desktop neon: Piri, tiles, glows
}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def load(path: str = paths.THEME) -> dict[str, str]:
    colors = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except FileNotFoundError:
        try:
            save(colors, path)
        except OSError:
            pass
        return colors
    except (OSError, ValueError):
        return colors
    if isinstance(saved, dict):
        for key in DEFAULTS:
            value = saved.get(key)
            if isinstance(value, str) and _HEX.match(value):
                colors[key] = value.lower()
    return colors


def save(colors: dict[str, str], path: str = paths.THEME) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: colors[k] for k in DEFAULTS}, f, indent=2)
        f.write("\n")
