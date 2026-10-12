"""Webcams through V4L2: devices, the resolutions they offer, their picture controls, and a preview.

- `v4l2-ctl --list-devices` names each camera and its /dev/video nodes; a camera usually has a
  second node for metadata, which offers no picture formats and is skipped.
- `v4l2-ctl --list-formats-ext` gives every pixel format with its sizes and frame rates.
- `v4l2-ctl --list-ctrls-menus` gives brightness, contrast, exposure, focus, ... with their ranges;
  `--set-ctrl` changes one. Controls stay set on the camera until it is unplugged, so every app
  (Discord, OBS, a browser) gets the adjusted picture.
- Each app picks its own resolution when it opens the camera; the list here shows what the camera
  can do, and the preview runs at the chosen one.
- The preview is ffmpeg reading the camera and writing small RGB frames to a pipe.

Parsers follow v4l2-ctl's own printf formats (v4l-utils 1.26).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

PREVIEW_SIZE = (640, 360)
PREVIEW_FPS = 15
# V4L2 fourcc -> ffmpeg's -input_format name
FFMPEG_FORMATS = {"MJPG": "mjpeg", "YUYV": "yuyv422", "H264": "h264", "NV12": "nv12", "YU12": "yuv420p",
                  "UYVY": "uyvy422", "RGB3": "rgb24", "GREY": "gray"}
CONTROL_NAME = re.compile(r"^[a-z0-9_]+$")


class CameraError(Exception):
    """A camera action that failed, with a message meant for the user."""


def run(*args: str, timeout: int = 8) -> str:
    if shutil.which("v4l2-ctl") is None:
        raise CameraError("Camera tools (v4l-utils) are not installed.")
    try:
        result = subprocess.run(["v4l2-ctl", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CameraError("The camera did not answer in time.") from None
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if "Device or resource busy" in message:
            raise CameraError("The camera is in use by another app.")
        raise CameraError(message.splitlines()[-1] if message else "v4l2-ctl failed.")
    return result.stdout


# --- parsers ----------------------------------------------------------------------------------------


def parse_list_devices(text: str) -> list[tuple[str, list[str]]]:
    """'Name (bus):' followed by indented /dev/... lines -> [(name, [/dev/video0, ...])]."""
    found: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace() and line.rstrip().endswith(":"):
            name = re.sub(r"\s*\([^()]*\):?$", "", line.rstrip().rstrip(":")).strip()
            found.append((name or line.strip(), []))
        elif found and line.strip().startswith("/dev/video"):
            found[-1][1].append(line.strip())
    return [(name, paths) for name, paths in found if paths]


@dataclass(frozen=True)
class Mode:
    fourcc: str
    width: int
    height: int
    fps: float

    @property
    def label(self) -> str:
        kind = "MJPEG" if self.fourcc == "MJPG" else self.fourcc
        return f"{self.width}×{self.height} · {self.fps:g} fps · {kind}"


def parse_formats(text: str) -> list[Mode]:
    """`--list-formats-ext`: the fastest frame rate of every (format, size)."""
    modes, fourcc, size, rates = [], "", None, []

    def flush():
        if fourcc and size and rates:
            modes.append(Mode(fourcc, size[0], size[1], max(rates)))

    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^\[\d+\]: '(\w+)'", line)
        if match:
            flush()
            fourcc, size, rates = match.group(1), None, []
            continue
        match = re.match(r"^Size: Discrete (\d+)x(\d+)", line)
        if match:
            flush()
            size, rates = (int(match.group(1)), int(match.group(2))), []
            continue
        match = re.match(r"^Interval: Discrete [\d.]+s \(([\d.]+) fps\)", line)
        if match:
            rates.append(float(match.group(1)))
    flush()
    return modes


def best_modes(modes: list[Mode]) -> list[Mode]:
    """One entry per size: the format with the most frames per second (MJPEG wins a tie, it is lighter
    on USB), largest size first."""
    best: dict[tuple[int, int], Mode] = {}
    for mode in modes:
        key = (mode.width, mode.height)
        current = best.get(key)
        if current is None or (mode.fps, mode.fourcc == "MJPG") > (current.fps, current.fourcc == "MJPG"):
            best[key] = mode
    return sorted(best.values(), key=lambda m: (m.width * m.height, m.fps), reverse=True)


@dataclass
class Control:
    name: str
    kind: str  # int, bool, menu, intmenu, ...
    minimum: int = 0
    maximum: int = 0
    step: int = 1
    default: int = 0
    value: int = 0
    inactive: bool = False  # e.g. manual exposure while auto exposure is on
    menu: dict[int, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()


CONTROL_LINE = re.compile(r"^\s*(\w+) 0x[0-9a-f]{8} \((\w+)\)\s*: (.*)$")


def parse_controls(text: str) -> list[Control]:
    """`--list-ctrls-menus`."""
    controls: list[Control] = []
    for line in text.splitlines():
        match = CONTROL_LINE.match(line)
        if match:
            values = dict(re.findall(r"(\w+)=(-?\d+|\S+)", match.group(3)))
            number = lambda key, fallback=0: int(values[key]) if re.match(r"^-?\d+$", values.get(key, "")) else fallback  # noqa: E731
            controls.append(Control(match.group(1), match.group(2), number("min"), number("max"), number("step", 1),
                                    number("default"), number("value"), "inactive" in values.get("flags", "")))
            continue
        match = re.match(r"^\t+(-?\d+): (.+)$", line)
        if match and controls and controls[-1].kind in ("menu", "intmenu"):
            controls[-1].menu[int(match.group(1))] = match.group(2).strip()
    return [c for c in controls if c.kind in ("int", "bool", "menu", "intmenu")]


# --- reading and changing ------------------------------------------------------------------------------------


@dataclass
class Camera:
    name: str
    path: str
    modes: list[Mode]


def cameras() -> list[Camera]:
    try:
        listing = run("--list-devices")
    except CameraError as error:
        if "not installed" in str(error):
            raise
        return []  # "Cannot open device /dev/video0": no camera plugged in
    found = []
    for name, paths in parse_list_devices(listing):
        for path in paths:
            try:
                modes = best_modes(parse_formats(run("-d", path, "--list-formats-ext")))
            except CameraError:
                continue
            if modes:
                found.append(Camera(name, path, modes))
                break
    return found


def _check_path(path: str) -> str:
    if not re.match(r"^/dev/video\d+$", path):
        raise CameraError(f"Not a camera: {path}")
    return path


def controls(path: str) -> list[Control]:
    return parse_controls(run("-d", _check_path(path), "--list-ctrls-menus"))


def set_control(path: str, name: str, value: int) -> None:
    if not CONTROL_NAME.match(name):
        raise CameraError(f"Unknown camera setting: {name}")
    run("-d", _check_path(path), f"--set-ctrl={name}={int(value)}")


def reset_controls(path: str, current: list[Control]) -> str:
    """Every setting back to the camera's default; automatic modes first, so the manual values they
    unlock can be set afterwards."""
    failed = []
    for control in sorted(current, key=lambda c: "auto" not in c.name):
        if control.value != control.default:
            try:
                set_control(path, control.name, control.default)
            except CameraError:
                failed.append(control.label)
    return "Camera settings are back to their defaults." + (
        f" Not changed: {', '.join(failed)}." if failed else "")


def preview_command(path: str, mode: Mode, size: tuple[int, int] = PREVIEW_SIZE) -> list[str]:
    """ffmpeg: read the camera at `mode`, write `size` RGB frames (letterboxed) to stdout."""
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2"]
    if mode.fourcc in FFMPEG_FORMATS:
        argv += ["-input_format", FFMPEG_FORMATS[mode.fourcc]]
    argv += ["-video_size", f"{mode.width}x{mode.height}", "-framerate", f"{mode.fps:g}", "-i", _check_path(path)]
    return argv + output_args(size)


def output_args(size: tuple[int, int] = PREVIEW_SIZE) -> list[str]:
    w, h = size
    return ["-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:-1:-1,fps={PREVIEW_FPS}",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]


def frame_bytes(size: tuple[int, int] = PREVIEW_SIZE) -> int:
    return size[0] * size[1] * 3
