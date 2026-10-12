#!/usr/bin/env python3
"""Build an X11/Wayland cursor theme from the PERDIX feather pointer.

Input:  one or more images of the pointer: PNG, or Windows .cur/.ico (the hotspot stored in
        a .cur is used to find the tip). Default: branding/cursor/*.cur
Output: <out>/<theme>/index.theme and <out>/<theme>/cursors/ in Xcursor format, which X11
        (libXcursor) and Wayland compositors (libwayland-cursor, wlroots, KWin, Mutter) all read.

The transparent padding around the artwork is trimmed so the feather's tip sits exactly at
pixel (0,0): that is the hotspot, the point that clicks. Each size comes from the best source
for thin pixel-art lines: an exact-size source is used as-is; enlarging (up to 2x) uses
nearest-neighbour so lines stay crisp; shrinking averages pixels and strengthens the lines'
opacity so they don't fade. --outline adds a thin dark edge for light backgrounds.

The feather replaces the normal arrow pointer only (left_ptr, default, arrow, ...). Text,
resize, busy and link cursors come from the inherited theme (Adwaita), because one arrow
can't stand in for them.

Usage:
  python3 tools/make-cursor-theme.py                         # writes rootfs/usr/share/icons/PERDIX-Feather
  python3 tools/make-cursor-theme.py pointer.png --out ~/.icons
  python3 tools/make-cursor-theme.py --sizes 24 32 48 64 96 --install user
Needs: Pillow.
"""

import argparse
import io
import os
import struct
import sys
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = sorted((ROOT / "branding" / "cursor").glob("*.cur"))
DEFAULT_OUT = ROOT / "rootfs" / "usr" / "share" / "icons"
THEME = "PERDIX-Feather"
INHERITS = "Adwaita"

# Names programs ask for when they want the ordinary arrow pointer.
ARROW_NAMES = ("left_ptr", "default", "arrow", "top_left_arrow", "left_arrow")

XCURSOR_MAGIC = b"Xcur"
XCURSOR_IMAGE_TYPE = 0xFFFD0002


# --- reading sources ------------------------------------------------------------------------------

def read_source(path: Path) -> list[tuple[Image.Image, tuple[int, int]]]:
    """(RGBA image, hotspot) pairs from a PNG or a .cur/.ico file."""
    data = path.read_bytes()
    if data[:4] in (b"\x00\x00\x02\x00", b"\x00\x00\x01\x00"):  # .cur / .ico directory
        count = struct.unpack_from("<H", data, 4)[0]
        images = []
        for i in range(count):
            _w, _h, _c, _r, hx, hy, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
            image = Image.open(io.BytesIO(data[offset:offset + size])).convert("RGBA")
            is_cursor = data[2] == 2  # .ico stores planes/bpp there, not a hotspot
            images.append((image, (hx, hy) if is_cursor else (0, 0)))
        return images
    return [(Image.open(path).convert("RGBA"), (0, 0))]


def trim(image: Image.Image, hotspot: tuple[int, int]) -> tuple[Image.Image, float]:
    """Crop to the artwork, starting at the hotspot, so the tip lands on (0,0).

    Returns the cropped art and how much of the canvas it filled (to keep proportions)."""
    left, top, right, bottom = image.getchannel("A").getbbox() or (0, 0, image.width, image.height)
    left, top = min(left, hotspot[0]), min(top, hotspot[1])
    art = image.crop((left, top, right, bottom))
    return art, max(art.width, art.height) / max(image.width, image.height)


def strengthen(image: Image.Image, factor: float) -> Image.Image:
    """Raise partial opacity so thin lines stay visible after shrinking."""
    r, g, b, a = image.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: min(255, int(v * factor)))))


def add_outline(image: Image.Image) -> Image.Image:
    """A 1 px dark edge around every line, so the hollow feather shows on light backgrounds."""
    halo = image.getchannel("A").filter(ImageFilter.MaxFilter(3)).point(lambda v: int(v * 0.85))
    out = Image.new("RGBA", image.size, (4, 6, 10, 0))
    out.putalpha(halo)
    out.alpha_composite(image)
    return out


def render(sources: list[tuple[Image.Image, tuple[int, int]]], size: int, outline: bool = False) -> Image.Image:
    """The pointer for one nominal size, art in the top-left corner of a size x size canvas."""
    by_size = sorted(sources, key=lambda s: max(s[0].size))
    exact = [s for s in by_size if max(s[0].size) == size]
    smaller = [s for s in by_size if size / 2 <= max(s[0].size) < size]
    if exact:
        art, _ = trim(*exact[0])
    else:
        image, hotspot = smaller[-1] if smaller else by_size[-1]
        art, _ = trim(image, hotspot)
        scale = size / max(image.size)
        target = (max(1, round(art.width * scale)), max(1, round(art.height * scale)))
        if scale > 1:   # enlarge: keep pixel-art lines crisp
            art = art.resize(target, Image.NEAREST)
        else:           # shrink: average, then restore line strength
            art = strengthen(art.resize(target, Image.BOX), 1.6)
    if outline:
        padded = Image.new("RGBA", (art.width + 1, art.height + 1), (0, 0, 0, 0))
        padded.alpha_composite(art)
        art = add_outline(padded)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(art.crop((0, 0, min(art.width, size), min(art.height, size))))
    return canvas


# --- writing Xcursor -----------------------------------------------------------------------------

def xcursor_bytes(images: list[tuple[int, Image.Image]], hotspot: tuple[int, int] = (0, 0)) -> bytes:
    """An Xcursor file holding one image per nominal size (see Xcursor(3))."""
    header = struct.pack("<4sIII", XCURSOR_MAGIC, 16, 0x10000, len(images))
    toc, chunks = b"", b""
    position = 16 + 12 * len(images)
    for nominal, image in images:
        rgba = image.convert("RGBA").tobytes()
        pixels = bytearray()
        for i in range(0, len(rgba), 4):
            r, g, b, a = rgba[i:i + 4]
            # ARGB, premultiplied alpha, little-endian 32-bit.
            pixels += struct.pack("<I", (a << 24) | (r * a // 255 << 16) | (g * a // 255 << 8) | (b * a // 255))
        chunk = struct.pack("<IIIIIIIII", 36, XCURSOR_IMAGE_TYPE, nominal, 1,
                            image.width, image.height, hotspot[0], hotspot[1], 0) + bytes(pixels)
        toc += struct.pack("<III", XCURSOR_IMAGE_TYPE, nominal, position)
        chunks += chunk
        position += len(chunk)
    return header + toc + chunks


def write_theme(out_dir: Path, theme: str, rendered: list[tuple[int, Image.Image]]) -> Path:
    theme_dir = out_dir / theme
    cursors = theme_dir / "cursors"
    cursors.mkdir(parents=True, exist_ok=True)
    (cursors / ARROW_NAMES[0]).write_bytes(xcursor_bytes(rendered))
    for name in ARROW_NAMES[1:]:
        link = cursors / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(ARROW_NAMES[0])
    (theme_dir / "index.theme").write_text(
        "[Icon Theme]\n"
        "Name=PERDIX Feather\n"
        "Comment=Hollow neon-blue feather pointer for PERDIX OS\n"
        f"Inherits={INHERITS}\n"
    )
    for size, image in rendered:  # previews, handy for checking the result
        image.save(theme_dir / f"preview-{size}.png")
    return theme_dir


def install_user(theme_dir: Path, theme: str) -> list[str]:
    """Copy the theme to ~/.icons and make it the default cursor.

    ~/.icons, not ~/.local/share/icons: every libXcursor and libwayland-cursor build searches
    ~/.icons, while some only search ~/.icons and /usr/share/icons."""
    import shutil
    target = Path.home() / ".icons" / theme
    if target.resolve() != theme_dir.resolve():
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(theme_dir, target, symlinks=True)
    notes = [f"Installed to {target}"]
    default = Path.home() / ".icons" / "default" / "index.theme"
    if default.exists() and f"Inherits={theme}" not in default.read_text():
        notes.append(f"Left {default} alone (it already sets another cursor theme); edit it to "
                     f"'Inherits={theme}' to switch.")
    else:
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_text(f"[Icon Theme]\nInherits={theme}\n")
        notes.append(f"Set as the default cursor theme in {default}")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sources", nargs="*", type=Path, default=DEFAULT_SOURCES,
                        help="pointer images (.png, .cur, .ico); default: branding/cursor/*.cur")
    parser.add_argument("--sizes", nargs="+", type=int, default=[24, 32, 48])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory to create the theme in")
    parser.add_argument("--name", default=THEME, help="theme name (folder name)")
    parser.add_argument("--outline", action="store_true",
                        help="add a thin dark edge (better on light backgrounds; changes the hollow look)")
    parser.add_argument("--install", choices=["user"], help="also install for the current user")
    args = parser.parse_args()

    if not args.sources:
        parser.error("no pointer images given and none found in branding/cursor/")
    sources = [entry for path in args.sources for entry in read_source(path)]
    rendered = [(size, render(sources, size, args.outline)) for size in sorted(set(args.sizes))]
    theme_dir = write_theme(args.out.expanduser(), args.name, rendered)
    print(f"Cursor theme written to {theme_dir} (sizes {', '.join(str(s) for s, _ in rendered)}, hotspot 0,0)")
    if args.install == "user":
        for note in install_user(theme_dir, args.name):
            print(note)
    print(f"""
Use it:
  X11 / most Wayland compositors:  export XCURSOR_THEME={args.name} XCURSOR_SIZE=32
  GNOME:     gsettings set org.gnome.desktop.interface cursor-theme {args.name}
  KDE:       System Settings > Colours & Themes > Cursors
  Hyprland:  hyprctl setcursor {args.name} 32
The theme inherits {INHERITS} for text, resize and busy cursors: install it too (Arch: adwaita-cursors).""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
