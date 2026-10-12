"""Reads /etc/kiosk/kiosk.conf (shell-style KEY=value lines). Environment variables override it."""

import os
import shlex

CONFIG_PATH = "/etc/kiosk/kiosk.conf"
DEFAULTS = {
    "KIOSK_STEAM_CMD": "steam -gamepadui",
}


def read_shell_vars(path: str) -> dict[str, str]:
    """Parse KEY=value lines (quotes and '#' comments allowed). A missing file gives {}."""
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    tokens = shlex.split(line, comments=True)
                except ValueError:
                    continue  # unbalanced quotes: skip the line rather than refuse to start
                if len(tokens) == 1 and "=" in tokens[0]:
                    key, value = tokens[0].split("=", 1)
                    values[key] = value
    except FileNotFoundError:
        pass
    return values


def load_config(path: str = CONFIG_PATH) -> dict[str, str]:
    config = dict(DEFAULTS)
    config.update(read_shell_vars(path))
    for key in config:
        if key in os.environ:
            config[key] = os.environ[key]
    return config
