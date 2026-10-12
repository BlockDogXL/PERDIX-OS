"""Game Mode & HUD: the PERDIX HUD theme engine for MangoHud, and Feral GameMode's status.

The theme engine edits ~/.config/MangoHud/MangoHud.conf in place: it replaces only the
options it manages and keeps every other line (and comment) the user added by hand.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field

MANGOHUD_CONFIG = os.path.expanduser("~/.config/MangoHud/MangoHud.conf")
LAUNCH_OPTIONS = "gamemoderun mangohud %command%"
HEADER = "# PERDIX HUD: options below are managed by PERDIX Control Center (Game Mode & HUD)."

POSITIONS = {"top-left": "Top-Left", "bottom-right": "Bottom-Right"}
# Colour themes: MangoHud colour options (RRGGBB) for each PERDIX HUD colour.
THEMES = {
    "orange": {"text_color": "FFD9C2", "cpu_color": "FF6A1A", "gpu_color": "FF8A4A",
               "frametime_color": "FF6A1A", "vram_color": "FFB27F", "ram_color": "FFB27F",
               "background_color": "0A0B0D"},
    "white": {"text_color": "FFFFFF", "cpu_color": "E6E6E6", "gpu_color": "FFFFFF",
              "frametime_color": "FFFFFF", "vram_color": "CFCFCF", "ram_color": "CFCFCF",
              "background_color": "000000"},
}
# MangoHud option -> label. Written as 1/0 so MangoHud's own defaults never sneak back in.
METRICS = {
    "fps": "FPS counter",
    "frame_timing": "Frame-time graph",
    "cpu_stats": "CPU load",
    "cpu_temp": "CPU temperature",
    "gpu_stats": "GPU load",
    "gpu_temp": "GPU temperature",
    "ram": "RAM usage",
    "vram": "VRAM usage",
    "gamemode": "GameMode status",
    "time": "Clock",
}
DEFAULT_METRICS = {"fps", "frame_timing", "cpu_stats", "gpu_stats", "gpu_temp"}


@dataclass
class HudSettings:
    color: str = "orange"
    position: str = "top-left"
    font_size: int = 20
    background_alpha: float = 0.4
    toggle_key: str = "Shift_R+F12"
    metrics: dict[str, bool] = field(default_factory=lambda: {m: m in DEFAULT_METRICS for m in METRICS})

    def __post_init__(self) -> None:
        if self.color not in THEMES:
            self.color = "orange"
        if self.position not in POSITIONS:
            self.position = "top-left"


def theme_options(color: str, position: str) -> dict[str, str]:
    if color not in THEMES or position not in POSITIONS:
        raise ValueError(f"unknown HUD theme {color!r} / {position!r}")
    return {"position": position, **THEMES[color]}


def layout_options(settings: HudSettings) -> dict[str, str]:
    options = {
        "font_size": str(settings.font_size),
        "background_alpha": f"{settings.background_alpha:.2f}",
        "toggle_hud": settings.toggle_key,
    }
    options.update({name: str(int(bool(settings.metrics.get(name)))) for name in METRICS})
    return options


def update_config(changes: dict[str, str], path: str = MANGOHUD_CONFIG) -> str:
    """Set `key=value` for each change in the MangoHud config, keeping all other lines."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    if HEADER not in lines:
        lines.insert(0, HEADER)
    remaining = dict(changes)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if not line.lstrip().startswith("#") and key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    lines += [f"{key}={value}" for key, value in remaining.items()]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


def apply_theme(color: str, position: str, path: str = MANGOHUD_CONFIG) -> str:
    """The PERDIX HUD theme engine: colour (orange/white) and corner (top-left/bottom-right)."""
    return update_config(theme_options(color, position), path)


def apply_settings(settings: HudSettings, path: str = MANGOHUD_CONFIG) -> str:
    return update_config({**theme_options(settings.color, settings.position), **layout_options(settings)}, path)


def mangohud_installed() -> bool:
    return shutil.which("mangohud") is not None


def gamemode_status() -> str:
    """'active', 'inactive', 'not installed' or 'unknown'."""
    if shutil.which("gamemoded") is None:
        return "not installed"
    try:
        output = subprocess.run(["gamemoded", "-s"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if "is active" in output:
        return "active"
    if "is inactive" in output:
        return "inactive"
    return "unknown"
