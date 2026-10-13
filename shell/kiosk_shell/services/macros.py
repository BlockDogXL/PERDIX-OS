"""Macro Studio model: macros made of key, text and delay steps, saved as JSON, played through uinput.

Playback creates a virtual keyboard with python-evdev, so it needs write access to
/dev/uinput (the ISO gives the 'input' group that access).
"""

import json
import os
import string
import time
from dataclasses import asdict, dataclass, field

from kiosk_shell import paths

MACROS_PATH = paths.MACROS
STEP_KINDS = ("key", "text", "delay")
MAX_DELAY_MS = 60_000

_ALIASES = {
    "ctrl": "KEY_LEFTCTRL", "shift": "KEY_LEFTSHIFT", "alt": "KEY_LEFTALT", "super": "KEY_LEFTMETA",
    "enter": "KEY_ENTER", "return": "KEY_ENTER", "esc": "KEY_ESC", "escape": "KEY_ESC", "tab": "KEY_TAB",
    "space": "KEY_SPACE", "backspace": "KEY_BACKSPACE", "delete": "KEY_DELETE", "insert": "KEY_INSERT",
    "home": "KEY_HOME", "end": "KEY_END", "pageup": "KEY_PAGEUP", "pagedown": "KEY_PAGEDOWN",
    "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
}
# US layout: character -> (key, needs shift)
_PUNCTUATION = {
    "-": ("KEY_MINUS", False), "_": ("KEY_MINUS", True), "=": ("KEY_EQUAL", False), "+": ("KEY_EQUAL", True),
    "[": ("KEY_LEFTBRACE", False), "{": ("KEY_LEFTBRACE", True), "]": ("KEY_RIGHTBRACE", False),
    "}": ("KEY_RIGHTBRACE", True), ";": ("KEY_SEMICOLON", False), ":": ("KEY_SEMICOLON", True),
    "'": ("KEY_APOSTROPHE", False), '"': ("KEY_APOSTROPHE", True), ",": ("KEY_COMMA", False),
    "<": ("KEY_COMMA", True), ".": ("KEY_DOT", False), ">": ("KEY_DOT", True), "/": ("KEY_SLASH", False),
    "?": ("KEY_SLASH", True), "\\": ("KEY_BACKSLASH", False), "|": ("KEY_BACKSLASH", True),
    "`": ("KEY_GRAVE", False), "~": ("KEY_GRAVE", True), " ": ("KEY_SPACE", False), "\n": ("KEY_ENTER", False),
}
_SHIFTED_DIGITS = dict(zip("!@#$%^&*()", "1234567890"))


@dataclass
class Step:
    kind: str  # "key" (a combo like ctrl+shift+r), "text" (typed out) or "delay" (milliseconds)
    value: str


@dataclass
class Macro:
    name: str
    steps: list[Step] = field(default_factory=list)


def parse_combo(text: str) -> list[str]:
    """'ctrl+shift+r' -> ['KEY_LEFTCTRL', 'KEY_LEFTSHIFT', 'KEY_R']. Raises ValueError if unknown."""
    keys = []
    for part in (p.strip().lower() for p in text.split("+")):
        if len(part) == 1 and part in string.ascii_lowercase + string.digits:
            keys.append(f"KEY_{part.upper()}")
        elif part in _ALIASES:
            keys.append(_ALIASES[part])
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            keys.append(f"KEY_{part.upper()}")
        else:
            raise ValueError(f"unknown key '{part or text}'")
    return keys


def text_to_keys(text: str) -> list[tuple[str, bool]]:
    """Characters -> (key, shift) for a US keyboard layout. Raises ValueError for others."""
    keys = []
    for char in text:
        if char in string.ascii_lowercase or char in string.digits:
            keys.append((f"KEY_{char.upper()}", False))
        elif char in string.ascii_uppercase:
            keys.append((f"KEY_{char}", True))
        elif char in _SHIFTED_DIGITS:
            keys.append((f"KEY_{_SHIFTED_DIGITS[char]}", True))
        elif char in _PUNCTUATION:
            keys.append(_PUNCTUATION[char])
        else:
            raise ValueError(f"can't type '{char}' (US layout only)")
    return keys


def validate(macro: Macro) -> list[str]:
    errors = []
    if not macro.name.strip():
        errors.append("The macro needs a name.")
    if not macro.steps:
        errors.append("Add at least one step.")
    for number, step in enumerate(macro.steps, 1):
        try:
            if step.kind == "key":
                parse_combo(step.value)
            elif step.kind == "text":
                if not step.value:
                    raise ValueError("text is empty")
                text_to_keys(step.value)
            elif step.kind == "delay":
                if not step.value.isdigit() or not 0 < int(step.value) <= MAX_DELAY_MS:
                    raise ValueError(f"delay must be 1-{MAX_DELAY_MS} ms")
            else:
                raise ValueError(f"unknown step type '{step.kind}'")
        except ValueError as error:
            errors.append(f"Step {number}: {error}.")
    return errors


def load_macros(path: str = MACROS_PATH) -> list[Macro]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [Macro(m["name"], [Step(s["kind"], str(s["value"])) for s in m["steps"]]) for m in data]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_macros(macros: list[Macro], path: str = MACROS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump([asdict(m) for m in macros], f, indent=2)
    os.replace(tmp, path)


def playback_available() -> tuple[bool, str]:
    try:
        import evdev  # noqa: F401
    except ImportError:
        return False, "Playback needs python-evdev."
    if not os.access("/dev/uinput", os.W_OK):
        return False, "Playback needs write access to /dev/uinput."
    return True, "Ready."


def play(macro: Macro, should_stop=lambda: False, key_gap: float = 0.015) -> None:
    """Type the macro on a temporary virtual keyboard. Call only after validate() passed."""
    from evdev import UInput, ecodes

    def code(name: str) -> int:
        return ecodes.ecodes[name]

    used = {"KEY_LEFTSHIFT"}
    for step in macro.steps:
        if step.kind == "key":
            used.update(parse_combo(step.value))
        elif step.kind == "text":
            used.update(key for key, _ in text_to_keys(step.value))

    def tap(keys: list[str]) -> None:
        for key in keys:
            device.write(ecodes.EV_KEY, code(key), 1)
            device.syn()
            time.sleep(key_gap)
        for key in reversed(keys):
            device.write(ecodes.EV_KEY, code(key), 0)
            device.syn()
            time.sleep(key_gap)

    with UInput({ecodes.EV_KEY: sorted(code(k) for k in used)}, name="PERDIX Macro Studio") as device:
        time.sleep(0.2)  # give the system a moment to pick up the new device
        for step in macro.steps:
            if should_stop():
                return
            if step.kind == "key":
                tap(parse_combo(step.value))
            elif step.kind == "text":
                for key, shift in text_to_keys(step.value):
                    tap(["KEY_LEFTSHIFT", key] if shift else [key])
            elif step.kind == "delay":
                end = time.monotonic() + int(step.value) / 1000
                while time.monotonic() < end and not should_stop():
                    time.sleep(0.01)
