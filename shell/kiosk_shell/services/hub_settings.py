"""PERDIX Control Center settings, stored as JSON in ~/.config/perdix/hub.json.

Sections: "mouse" (device, speed, flat), "crosshair" and "hud". Unknown or broken
content falls back to defaults instead of stopping the hub from starting.
"""

import json
import os

HUB_SETTINGS_PATH = os.path.expanduser("~/.config/perdix/hub.json")


def load(path: str = HUB_SETTINGS_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_section(name: str, values: dict, path: str = HUB_SETTINGS_PATH) -> None:
    data = load(path)
    data[name] = values
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def merged(defaults: dict, saved: object) -> dict:
    """Defaults overridden by saved values of the same type (so bad JSON can't break the UI)."""
    result = dict(defaults)
    if isinstance(saved, dict):
        for key, default in defaults.items():
            value = saved.get(key)
            if isinstance(default, bool):
                if isinstance(value, bool):
                    result[key] = value
            elif isinstance(default, (int, float)) and isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] = type(default)(value)
            elif isinstance(default, str) and isinstance(value, str):
                result[key] = value
            elif isinstance(default, dict) and isinstance(value, dict):
                result[key] = merged(default, value)
    return result
