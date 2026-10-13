"""PERDIX Vision: game image tuning (brightness, contrast, vibrance, sharpening, shadow boost and
3D LUTs) applied by gamescope while games run.

How it reaches the screen, with nothing added to the game's own frame pacing:
- Colour (everything but sharpening) is baked into one 3D LUT, written as a .cube file and handed to
  gamescope through the GAMESCOPE_COLOR_LOOK_G22 root-window property. gamescope reloads it at once
  and applies it in the colour-management step it already runs for every frame (on AMD GPUs with
  display colour management, in the display hardware itself).
- Sharpening is contrast adaptive sharpening (resources/vision/PerdixCAS.fxh), run by gamescope's
  ReShade support (GAMESCOPE_RESHADE_EFFECT) on the game image at native resolution.
- A user's own LUT (.cube from DaVinci Resolve, Photoshop, TacticalVision exports, ...) is read
  with its own domain and combined with the sliders into that single LUT, because gamescope's
  .cube reader assumes a 0-1 domain.

Games run inside gamescope when Vision is on: the desktop then starts Steam as
`gamescope -e -f -W <w> -H <h> -- steam -gamepadui` (see gamescope_argv), and the session writes
its X display to $XDG_RUNTIME_DIR/perdix/gamescope-display so the Control Center can change the
root-window properties live (python-xlib; xprop would silently skip atoms gamescope never created). Property names and the .cube format follow gamescope's own sources
(steamcompmgr.cpp, color_helpers.cpp). Applies to SDR games; HDR output has its own LUT slot.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np

from kiosk_shell import paths

LUT_DIR = Path(paths.USER_DIR) / "vision" / "luts"  # the user's imported .cube files
STATE_DIR = Path(os.path.expanduser("~/.local/state/perdix/vision"))  # generated files
LOOK_FILE = STATE_DIR / "perdix-vision.cube"
SHADER_DIR = Path(os.path.expanduser("~/.local/share/gamescope/reshade/Shaders"))  # where gamescope looks
SHADER_TEMPLATE = Path(__file__).resolve().parent.parent / "resources" / "vision" / "PerdixCAS.fxh"
LUT_SIZE = 33  # edge of the generated LUT: 33^3 points, the usual size for display LUTs
MAX_LUT_BYTES = 32 * 1024 * 1024
MAX_LUT_EDGE = 128  # gamescope rejects larger .cube files

ATOM_LOOK = "GAMESCOPE_COLOR_LOOK_G22"
ATOM_RESHADE = "GAMESCOPE_RESHADE_EFFECT"
ATOM_RESHADE_TECHNIQUE = "GAMESCOPE_RESHADE_TECHNIQUE_IDX"


class VisionError(Exception):
    """A problem with a LUT file or with gamescope, with a message meant for the user."""


# --- settings -------------------------------------------------------------------------------------


@dataclass
class VisionSettings:
    enabled: bool = False  # run games through gamescope with these settings
    brightness: int = 0  # -50..50
    contrast: int = 0  # -50..50
    vibrance: int = 0  # -100..100: saturation that boosts dull colours more than vivid ones
    sharpening: int = 0  # 0..100
    lift: int = 0  # -50..50: shadow floor
    gamma: int = 0  # -50..50: mid-tones (positive brightens dark areas)
    gain: int = 0  # -50..50: highlights
    lut: str = ""  # file name in LUT_DIR, or "" for none
    lut_strength: int = 100  # 0..100

    RANGES = {"brightness": (-50, 50), "contrast": (-50, 50), "vibrance": (-100, 100), "sharpening": (0, 100),
              "lift": (-50, 50), "gamma": (-50, 50), "gain": (-50, 50), "lut_strength": (0, 100)}

    @classmethod
    def from_dict(cls, data: object) -> "VisionSettings":
        settings = cls()
        if not isinstance(data, dict):
            return settings
        for f in fields(cls):
            value = data.get(f.name)
            if f.name in cls.RANGES and isinstance(value, (int, float)) and not isinstance(value, bool):
                low, high = cls.RANGES[f.name]
                setattr(settings, f.name, int(max(low, min(high, round(value)))))
            elif f.name == "enabled" and isinstance(value, bool):
                settings.enabled = value
            elif f.name == "lut" and isinstance(value, str) and safe_lut_name(value):
                settings.lut = value
        return settings

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def colour_neutral(self) -> bool:
        """True when the colour LUT would change nothing."""
        tuning = (self.brightness, self.contrast, self.vibrance, self.lift, self.gamma, self.gain)
        return not any(tuning) and (not self.lut or self.lut_strength == 0)


# --- .cube files ----------------------------------------------------------------------------------


@dataclass
class CubeLut:
    size: int
    table: np.ndarray  # (size, size, size, 3), indexed [blue][green][red]: red changes fastest
    domain_min: np.ndarray
    domain_max: np.ndarray
    title: str = ""


def parse_cube(text: str) -> CubeLut:
    """A 3D LUT in the Adobe/Resolve .cube format. Raises VisionError for anything else."""
    size, title = 0, ""
    domain_min, domain_max = np.zeros(3), np.ones(3)
    values: list[list[float]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        keyword = line.split()[0].upper()
        if keyword == "TITLE":
            title = line[5:].strip().strip('"')
        elif keyword == "LUT_1D_SIZE":
            raise VisionError("This is a 1D LUT; PERDIX Vision needs a 3D LUT (LUT_3D_SIZE).")
        elif keyword == "LUT_3D_SIZE":
            try:
                size = int(line.split()[1])
            except (IndexError, ValueError):
                raise VisionError(f"Line {number}: LUT_3D_SIZE needs a number.") from None
            if not 2 <= size <= MAX_LUT_EDGE:
                raise VisionError(f"LUT size {size} is not supported (2 to {MAX_LUT_EDGE}).")
        elif keyword in ("DOMAIN_MIN", "DOMAIN_MAX"):
            triple = _floats(line.split()[1:], number)
            if keyword == "DOMAIN_MIN":
                domain_min = np.array(triple)
            else:
                domain_max = np.array(triple)
        elif keyword == "LUT_3D_INPUT_RANGE":  # Resolve's variant: one min and one max for all channels
            low, high = _floats(line.split()[1:], number, count=2)
            domain_min, domain_max = np.full(3, low), np.full(3, high)
        elif re.match(r"^[-+.\d]", keyword):
            values.append(_floats(line.split(), number))
        # Other keywords (LUT_IN_VIDEO_RANGE, comments by tools, ...) are ignored, as Resolve does.
    if size == 0:
        raise VisionError("Not a 3D LUT: no LUT_3D_SIZE line.")
    if len(values) != size ** 3:
        raise VisionError(f"The LUT should have {size ** 3} entries but has {len(values)}.")
    if np.any(domain_max <= domain_min):
        raise VisionError("DOMAIN_MAX must be larger than DOMAIN_MIN.")
    table = np.array(values, dtype=np.float64).reshape(size, size, size, 3)
    return CubeLut(size, table, domain_min, domain_max, title)


def _floats(parts: list[str], number: int, count: int = 3) -> list[float]:
    try:
        result = [float(p) for p in parts[:count]]
    except ValueError:
        raise VisionError(f"Line {number}: expected {count} numbers.") from None
    if len(result) != count or len(parts) != count or not all(np.isfinite(result)):
        raise VisionError(f"Line {number}: expected {count} numbers.")
    return result


def sample(lut: CubeLut, rgb: np.ndarray) -> np.ndarray:
    """Trilinear lookup of an (..., 3) array of RGB values."""
    pos = (rgb - lut.domain_min) / (lut.domain_max - lut.domain_min)
    pos = np.clip(pos, 0.0, 1.0) * (lut.size - 1)
    lo = np.minimum(np.floor(pos).astype(int), lut.size - 2)
    frac = pos - lo
    r0, g0, b0 = lo[..., 0], lo[..., 1], lo[..., 2]
    fr, fg, fb = frac[..., 0:1], frac[..., 1:2], frac[..., 2:3]
    t = lut.table
    c00 = t[b0, g0, r0] * (1 - fr) + t[b0, g0, r0 + 1] * fr
    c10 = t[b0, g0 + 1, r0] * (1 - fr) + t[b0, g0 + 1, r0 + 1] * fr
    c01 = t[b0 + 1, g0, r0] * (1 - fr) + t[b0 + 1, g0, r0 + 1] * fr
    c11 = t[b0 + 1, g0 + 1, r0] * (1 - fr) + t[b0 + 1, g0 + 1, r0 + 1] * fr
    c0 = c00 * (1 - fg) + c10 * fg
    c1 = c01 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def cube_text(table: np.ndarray, title: str = "PERDIX Vision") -> str:
    """The .cube text gamescope's reader expects: LUT_3D_SIZE, then one 'r g b' line per entry with
    red changing fastest, '.' as the decimal point, 0-1 domain."""
    size = table.shape[0]
    header = f'TITLE "{title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n'
    rows = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in np.clip(table, 0.0, 1.0).reshape(-1, 3))
    return header + rows + "\n"


# --- the colour pipeline ----------------------------------------------------------------------------

LUMA = np.array([0.2126, 0.7152, 0.0722])  # Rec. 709 / sRGB


def adjust(rgb: np.ndarray, s: VisionSettings) -> np.ndarray:
    """Shadow boost (lift/gamma/gain), then brightness and contrast, then vibrance. Values 0-1."""
    x = np.clip(rgb, 0.0, 1.0)
    lift, gain = s.lift / 200, 1 + s.gain / 100  # lift ±0.25 of the floor, gain ×0.5-1.5
    gamma = 2.0 ** (s.gamma / 50)  # 0.5-2.0; above 1 opens up the dark areas
    x = np.clip(gain * (x + lift * (1 - x)), 0.0, 1.0) ** (1 / gamma)
    x = x + s.brightness / 200  # ±0.25
    x = (x - 0.5) * (1 + s.contrast / 100) + 0.5  # ×0.5-1.5 around mid-grey
    x = np.clip(x, 0.0, 1.0)
    if s.vibrance:
        k = s.vibrance / 100
        luma = (x @ LUMA)[..., None]
        if k > 0:  # vibrance: dull colours gain more than already saturated ones
            chroma = (x.max(axis=-1) - x.min(axis=-1))[..., None]
            factor = 1 + k * (1 - chroma)
        else:  # down to grey at -100
            factor = 1 + k
        x = luma + (x - luma) * factor
    return np.clip(x, 0.0, 1.0)


def transform(rgb: np.ndarray, s: VisionSettings, lut: CubeLut | None = None) -> np.ndarray:
    """The whole look: the user's LUT (blended by its strength) first, then the sliders."""
    x = np.clip(rgb, 0.0, 1.0)
    if lut is not None and s.lut_strength:
        x = x + (sample(lut, x) - x) * (s.lut_strength / 100)
    return adjust(x, s)


def build_table(s: VisionSettings, lut: CubeLut | None = None, size: int = LUT_SIZE) -> np.ndarray:
    grid = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")  # [blue][green][red]: red fastest
    return transform(np.stack([r, g, b], axis=-1), s, lut)


# --- the LUT library ----------------------------------------------------------------------------------


def safe_lut_name(name: str) -> bool:
    return bool(re.fullmatch(r"[\w .()+-]{1,96}\.cube", name)) and not name.startswith(".")


def list_luts(folder: Path | None = None) -> list[str]:
    folder = folder or LUT_DIR
    return sorted((p.name for p in folder.glob("*.cube") if safe_lut_name(p.name)), key=str.lower)


def load_lut(name: str, folder: Path | None = None) -> CubeLut:
    folder = folder or LUT_DIR
    if not safe_lut_name(name):
        raise VisionError("Invalid LUT name.")
    try:
        return parse_cube((folder / name).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        raise VisionError(f"{name} could not be read.") from None


def import_lut(source: str, folder: Path | None = None) -> str:
    """Check a .cube file and copy it into the library; returns its name there."""
    folder = folder or LUT_DIR
    path = Path(source)
    if path.suffix.lower() != ".cube":
        raise VisionError("Choose a .cube file.")
    try:
        if path.stat().st_size > MAX_LUT_BYTES:
            raise VisionError("That LUT file is too large.")
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise VisionError(f"{path.name} could not be read.") from None
    parse_cube(text)  # raises with the reason if it isn't a usable 3D LUT
    stem = re.sub(r"[^\w .()+-]", "_", path.stem).strip(" .") or "LUT"
    stem = stem[:90]
    folder.mkdir(parents=True, exist_ok=True)
    name, n = f"{stem}.cube", 2
    while (folder / name).exists():
        name, n = f"{stem} ({n}).cube", n + 1
    (folder / name).write_text(text, encoding="utf-8")
    return name


def delete_lut(name: str, folder: Path | None = None) -> None:
    folder = folder or LUT_DIR
    if not safe_lut_name(name):
        raise VisionError("Invalid LUT name.")
    (folder / name).unlink(missing_ok=True)


# --- files for gamescope ---------------------------------------------------------------------------


def shader_name(sharpening: int) -> str:
    return f"perdix_cas_{sharpening:03d}.fx"


def write_look(s: VisionSettings, path: Path | None = None, luts: Path | None = None) -> Path | None:
    """The combined LUT for gamescope, or None when the colour is untouched."""
    path, luts = path or LOOK_FILE, luts or LUT_DIR
    if s.colour_neutral:
        return None
    lut = None
    if s.lut:
        try:
            lut = load_lut(s.lut, luts)
        except VisionError:
            lut = None  # deleted or broken since it was chosen: the sliders still apply
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".cube.tmp")
    tmp.write_text(cube_text(build_table(s, lut)), encoding="utf-8")
    os.replace(tmp, path)  # gamescope never sees a half-written file
    return path


def write_shader(sharpening: int, folder: Path | None = None) -> str | None:
    """The sharpening effect for gamescope, or None at 0. A new file name per strength, because
    gamescope keeps an effect that is set again under the same name."""
    folder = folder or SHADER_DIR
    keep = shader_name(sharpening) if sharpening else ""
    for old in folder.glob("perdix_cas_*.fx"):  # nothing to do if the folder doesn't exist yet
        if old.name != keep:
            old.unlink(missing_ok=True)
    if not sharpening:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SHADER_TEMPLATE, folder / "PerdixCAS.fxh")
    (folder / keep).write_text(f'#define PERDIX_SHARPNESS {sharpening / 100:.2f}\n#include "PerdixCAS.fxh"\n',
                               encoding="utf-8")
    return keep


# --- gamescope ------------------------------------------------------------------------------------------


def gamescope_installed() -> bool:
    return shutil.which("gamescope") is not None


def display_file() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "perdix" / "gamescope-display"


def session_display() -> str | None:
    """The X display of the running PERDIX gamescope session, if there is one."""
    try:
        display, pid = display_file().read_text().split()[:2]
    except (OSError, ValueError):
        return None
    return display if pid.isdigit() and os.path.exists(f"/proc/{pid}") else None


def _root_change(display: str, change) -> None:
    """Open gamescope's X display, call change(connection, root window), flush, close."""
    from Xlib import display as xdisplay  # python-xlib

    try:
        connection = xdisplay.Display(display)
    except Exception:  # Xlib raises several kinds of errors for an unreachable display
        raise VisionError("Could not reach gamescope.") from None
    try:
        change(connection, connection.screen().root)
        connection.sync()
    finally:
        connection.close()


def set_string(display: str, atom: str, value: str) -> None:
    """A text property, as gamescope writes its own (UTF8_STRING, 8 bit)."""
    _root_change(display, lambda d, root: root.change_property(
        d.intern_atom(atom), d.intern_atom("UTF8_STRING"), 8, value.encode()))


def set_cardinal(display: str, atom: str, value: int) -> None:
    from Xlib import Xatom

    _root_change(display, lambda d, root: root.change_property(d.intern_atom(atom), Xatom.CARDINAL, 32, [value]))


def remove(display: str, atom: str) -> None:
    _root_change(display, lambda d, root: root.delete_property(d.intern_atom(atom)))


def apply(s: VisionSettings) -> str:
    """Write the look and the sharpening effect and, if games are running, switch gamescope to them
    at once. Returns a message for the user. Takes a fraction of a second (LUT generation)."""
    look = write_look(s) if s.enabled else None
    shader = write_shader(s.sharpening if s.enabled else 0)
    display = session_display()
    if display is None:
        if not s.enabled:
            return "PERDIX Vision is off."
        if not gamescope_installed():
            raise VisionError("gamescope is not installed.")
        return "Saved. Applied to games as soon as you open Steam."
    if look is not None:
        set_string(display, ATOM_LOOK, str(look))
    else:
        remove(display, ATOM_LOOK)
    if shader is not None:
        set_cardinal(display, ATOM_RESHADE_TECHNIQUE, 0)
        set_string(display, ATOM_RESHADE, shader)
    else:
        remove(display, ATOM_RESHADE)
    return "Applied live to the running games." if s.enabled else "Vision effects removed from the running games."


def gamescope_argv(steam_argv: list[str], width: int, height: int, python: str, package_root: str) -> list[str]:
    """Steam inside gamescope, full screen at the screen's size. The inner shell records gamescope's
    X display (for live changes), applies the saved Vision settings, then becomes Steam."""
    record = 'mkdir -p "$XDG_RUNTIME_DIR/perdix" && printf "%s %s\\n" "$DISPLAY" "$$" >"$XDG_RUNTIME_DIR/perdix/gamescope-display"'
    restore = f"PYTHONPATH={shlex.quote(package_root)} {shlex.quote(python)} -m kiosk_shell.services.vision --apply-saved"
    inner = f"{record}; {restore} >/dev/null 2>&1; exec {shlex.join(steam_argv)}"
    return ["gamescope", "-e", "-f", "-W", str(width), "-H", str(height), "--", "sh", "-c", inner]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PERDIX Vision")
    parser.add_argument("--apply-saved", action="store_true", help="apply the saved settings to gamescope")
    args = parser.parse_args(argv)
    if args.apply_saved:
        from kiosk_shell.services import hub_settings
        try:
            print(apply(VisionSettings.from_dict(hub_settings.load().get("vision"))))
        except VisionError as error:
            print(f"PERDIX Vision: {error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
