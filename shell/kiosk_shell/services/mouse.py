"""Mouse acceleration through xinput (X11 + libinput), and sensitivity maths for games.

Acceleration curves work like Raw Accel on Windows: a sensitivity multiplier that grows
with hand speed. They are sent to libinput's *custom* acceleration profile (libinput 1.23+,
xf86-input-libinput 1.3+) as a table of output speeds, one point per count/ms of input speed.
"""

import math
import re
import shutil
import subprocess
from dataclasses import dataclass

# Degrees the camera turns per mouse count at in-game sensitivity 1.0 ("yaw").
GAMES = {
    "Counter-Strike 2": 0.022,
    "Apex Legends": 0.022,
    "Team Fortress 2": 0.022,
    "Valorant": 0.07,
    "Overwatch 2": 0.0066,
}

SPEED_PROP = "libinput Accel Speed"
PROFILE_PROP = "libinput Accel Profile Enabled"  # values: adaptive, flat[, custom]
POINTS_PROP = "libinput Accel Custom Motion Points"
STEP_PROP = "libinput Accel Custom Motion Step"
ADAPTIVE, FLAT, CUSTOM = 0, 1, 2

# Acceleration curves. "off" is libinput's flat profile (raw input, no acceleration).
CURVES = {
    "off": "Off (raw / flat)",
    "linear": "Linear",
    "natural": "Natural",
    "classic": "Classic",
}
MAX_SPEED = 50.0   # counts per ms covered by the table; libinput extrapolates beyond it
STEP = 1.0         # 51 points: 0..50 (libinput accepts up to 64)
CAP = 4.0          # highest multiplier the linear and classic curves may reach


def multiplier(curve: str, amount: float, speed: float) -> float:
    """Sensitivity multiplier at a hand speed (counts/ms). amount is 0..1 (the slider)."""
    amount = max(0.0, min(1.0, amount))
    if curve == "linear":       # grows at a constant rate, like Raw Accel's Linear
        return min(CAP, 1.0 + 0.08 * amount * speed)
    if curve == "classic":      # slow start, then steeper (power 2), like Raw Accel's Classic
        return min(CAP, 1.0 + (0.08 * amount * speed) ** 2)
    if curve == "natural":      # rises quickly then levels off at 1 + 3 * amount, like Natural
        return 1.0 + 3.0 * amount * (1.0 - math.exp(-0.08 * speed))
    return 1.0


def custom_points(curve: str, amount: float) -> list[float]:
    """Output speed for each input speed 0, STEP, 2*STEP, ... MAX_SPEED (libinput's format)."""
    count = int(MAX_SPEED / STEP) + 1
    return [round(i * STEP * multiplier(curve, amount, i * STEP), 4) for i in range(count)]


def edpi(dpi: float, sens: float) -> float:
    return dpi * sens


def cm_per_360(dpi: float, sens: float, yaw: float) -> float:
    """Mouse travel in centimetres for one full 360° turn."""
    counts = 360.0 / (sens * yaw)
    return counts / dpi * 2.54


def convert_sens(sens: float, from_yaw: float, to_yaw: float) -> float:
    """Sensitivity that gives the same cm/360 in another game at the same DPI."""
    return sens * from_yaw / to_yaw


@dataclass
class Pointer:
    id: int
    name: str


_POINTER_LINE = re.compile(r"↳\s*(?P<name>.+?)\s+id=(?P<id>\d+)\s+\[slave\s+pointer")


def parse_pointer_list(text: str) -> list[Pointer]:
    """Real pointer devices from `xinput list` (the virtual XTEST pointer is skipped)."""
    pointers = []
    for line in text.splitlines():
        match = _POINTER_LINE.search(line)
        if match and "XTEST" not in match["name"]:
            pointers.append(Pointer(int(match["id"]), match["name"].strip()))
    return pointers


def parse_props(text: str) -> dict[str, str]:
    """`xinput list-props` lines like '\\tlibinput Accel Speed (318):\\t0.000000' -> {name: value}."""
    props = {}
    for line in text.splitlines():
        name, sep, value = line.strip().partition(":")
        if sep:
            props[re.sub(r"\s*\(\d+\)$", "", name)] = value.strip()
    return props


def profile_values(current: str, profile: int) -> list[str]:
    """New 'Accel Profile Enabled' values with the same length as the device reports."""
    count = max(2, len([v for v in current.split(",") if v.strip()]))
    if profile >= count:
        raise ValueError("profile not supported by this device")
    values = ["0"] * count
    values[profile] = "1"
    return values


class XInput:
    """Thin wrapper around the xinput command. Every call returns (ok, message)."""

    def __init__(self) -> None:
        self.available = shutil.which("xinput") is not None

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["xinput", *args], capture_output=True, text=True, timeout=5)

    def pointers(self) -> list[Pointer]:
        if not self.available:
            return []
        try:
            return parse_pointer_list(self._run("list").stdout)
        except (OSError, subprocess.TimeoutExpired):
            return []

    def apply_curve(self, pointer_id: int, curve: str, amount: float) -> tuple[bool, str]:
        """Switch the device to a curve: 'off' = flat profile, otherwise libinput's custom profile."""
        if not self.available:
            return False, "xinput is not installed (package 'xorg-xinput')."
        try:
            props = parse_props(self._run("list-props", str(pointer_id)).stdout)
            if PROFILE_PROP not in props:
                return False, "This device has no libinput acceleration settings."
            if curve == "off":
                steps = [("set-prop", str(pointer_id), SPEED_PROP, "0"),
                         ("set-prop", str(pointer_id), PROFILE_PROP, *profile_values(props[PROFILE_PROP], FLAT))]
                done = "Acceleration off: flat profile (raw input)."
            else:
                if POINTS_PROP not in props:
                    return False, "Custom curves need libinput 1.23+ and xf86-input-libinput 1.3+."
                points = [f"{v:.4f}" for v in custom_points(curve, amount)]
                steps = [("set-prop", str(pointer_id), STEP_PROP, f"{STEP:.1f}"),
                         ("set-prop", str(pointer_id), POINTS_PROP, *points),
                         ("set-prop", str(pointer_id), PROFILE_PROP, *profile_values(props[PROFILE_PROP], CUSTOM))]
                done = f"{CURVES[curve]} curve applied at {amount:.0%}."
            for step in steps:
                result = self._run(*step)
                if result.returncode != 0:
                    return False, result.stderr.strip() or "xinput failed."
        except ValueError as error:
            return False, f"This device can't use that profile ({error})."
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, str(error)
        return True, done
