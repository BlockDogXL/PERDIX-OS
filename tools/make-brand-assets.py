#!/usr/bin/env python3
"""Generate every Piri logo variant from the master artwork.

Input:  branding/piri-master.png  (the red-on-white Perdix perdix logo, any size, square)
Output: branding/generated/       (PNG files with transparency, see `variants` below)
        rootfs/usr/share/plymouth/themes/perdix/   (boot/shutdown splash images)
        shell/kiosk_shell/resources/brand/          (masks the desktop recolours itself)

Usage:  python3 tools/make-brand-assets.py
Needs:  Pillow and numpy.

The master is split into two masks:
  strokes     the red artwork itself (anti-aliased)
  silhouette  the bird's outer outline, filled solid (used for small icons and glows)
All colours come from branding/BRANDING.md.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "branding" / "piri-master.png"
OUT = ROOT / "branding" / "generated"
PLYMOUTH_THEME = ROOT / "rootfs" / "usr" / "share" / "plymouth" / "themes" / "perdix"
SHELL_RESOURCES = ROOT / "shell" / "kiosk_shell" / "resources" / "brand"

TURKISH_RED = (227, 10, 23)      # #E30A17, the Turkish flag red
MASTER_RED = (158, 26, 32)       # the red used in the master artwork
NEON_ORANGE = (255, 106, 26)     # #FF6A1A
WHITE = (255, 255, 255)
NEAR_BLACK = (12, 12, 14)
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def load_masks(size: int = 1024) -> tuple[Image.Image, Image.Image]:
    rgb = np.asarray(Image.open(MASTER).convert("RGB").resize((size, size), Image.LANCZOS)).astype(float)
    redness = rgb[..., 0] - (rgb[..., 1] + rgb[..., 2]) / 2
    strokes = Image.fromarray((np.clip(redness / 138.0, 0, 1) * 255).astype(np.uint8), "L")
    # Silhouette: flood the background from a corner; whatever the flood can't reach is the bird.
    flood = strokes.point(lambda v: 255 if v > 100 else 0)
    ImageDraw.floodfill(flood, (0, 0), 128)
    silhouette = flood.point(lambda v: 0 if v == 128 else 255)
    silhouette = silhouette.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    return crop_square(strokes, silhouette)


def crop_square(*masks: Image.Image, pad: float = 0.04) -> tuple[Image.Image, ...]:
    left, top, right, bottom = masks[-1].getbbox()
    side = int(max(right - left, bottom - top) * (1 + 2 * pad))
    cx, cy = (left + right) // 2, (top + bottom) // 2
    box = (cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side)
    return tuple(m.crop(box) for m in masks)


def solid(mask: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    layer = Image.new("RGBA", mask.size, color + (0,))
    layer.putalpha(mask)
    return layer


def two_tone(strokes: Image.Image, silhouette: Image.Image, line: tuple, fill: tuple) -> Image.Image:
    """Bird filled with `fill`, artwork drawn on top in `line` (the look of the dark-background logos)."""
    image = solid(silhouette, fill)
    image.alpha_composite(solid(strokes, line))
    return image


def with_glow(art: Image.Image, silhouette: Image.Image, color: tuple, canvas: float = 1.5) -> Image.Image:
    """Centre `art` on a larger transparent canvas with a soft neon glow around its outline."""
    side = int(art.width * canvas)
    offset = ((side - art.width) // 2, (side - art.height) // 2)
    base = Image.new("L", (side, side), 0)
    base.paste(silhouette, offset)
    out = Image.new("RGBA", (side, side), color + (0,))
    for radius, strength in ((side * 0.06, 0.55), (side * 0.02, 0.9)):
        glow = base.filter(ImageFilter.GaussianBlur(radius)).point(lambda v, s=strength: int(min(255, v * s * 1.6)))
        out.alpha_composite(solid(glow, color))
    # A thin bright rim, like a neon tube along the outline.
    rim = ImageChops.subtract(base.filter(ImageFilter.MaxFilter(7)), base)
    out.alpha_composite(solid(rim, color))
    out.alpha_composite(art, offset)
    return out


def icon(silhouette: Image.Image, color: tuple, size: int) -> Image.Image:
    # Downscale in two steps so thin parts (legs, tail tips) keep some weight at 16-24 px.
    mask = silhouette.resize((size * 4, size * 4), Image.LANCZOS).filter(ImageFilter.MaxFilter(3))
    return solid(mask.resize((size, size), Image.LANCZOS), color)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    strokes, silhouette = load_masks()

    variants = {
        # Boot/shutdown splash and lock-screen seal (dark backgrounds).
        "piri-neon-orange.png": with_glow(two_tone(strokes, silhouette, NEON_ORANGE, WHITE), silhouette, NEON_ORANGE),
        "piri-neon-white.png": with_glow(two_tone(strokes, silhouette, NEAR_BLACK, WHITE), silhouette, WHITE),
        # About System / control panel: large, no glow.
        "piri-orange.png": two_tone(strokes, silhouette, NEON_ORANGE, WHITE),
        "piri-mono-dark.png": two_tone(strokes, silhouette, NEAR_BLACK, WHITE),
        # The original red-and-white artwork with a transparent background, master red and flag red.
        "piri-red.png": solid(strokes, MASTER_RED),
        "piri-turkish-red.png": solid(strokes, TURKISH_RED),
        # Masks for code that recolours the logo itself (e.g. the Qt desktop).
        "piri-strokes-mask.png": solid(strokes, WHITE),
        "piri-silhouette-mask.png": solid(silhouette, WHITE),
    }
    for name, image in variants.items():
        image.save(OUT / name, optimize=True)

    # Taskbar / start-menu icons: the filled silhouette, which stays readable at 16 px.
    for size in ICON_SIZES:
        icon(silhouette, WHITE, size).save(OUT / f"piri-icon-white-{size}.png", optimize=True)
        icon(silhouette, NEAR_BLACK, size).save(OUT / f"piri-icon-black-{size}.png", optimize=True)

    # Full-screen boot backgrounds (what Plymouth shows), 1920x1080.
    for name, art, bg in (
        ("boot-turkish-1080p.png", variants["piri-turkish-red.png"], WHITE),
        ("boot-neon-orange-1080p.png", variants["piri-neon-orange.png"], NEAR_BLACK),
    ):
        screen = Image.new("RGBA", (1920, 1080), bg + (255,))
        logo = art.resize((560, 560) if "neon" in name else (420, 420), Image.LANCZOS)
        screen.alpha_composite(logo, ((1920 - logo.width) // 2, (1080 - logo.height) // 2 - 40))
        screen.convert("RGB").save(OUT / name, optimize=True)

    # Copies used by the system itself.
    PLYMOUTH_THEME.mkdir(parents=True, exist_ok=True)
    variants["piri-turkish-red.png"].save(PLYMOUTH_THEME / "piri-red.png", optimize=True)
    variants["piri-neon-orange.png"].save(PLYMOUTH_THEME / "piri-glow.png", optimize=True)
    SHELL_RESOURCES.mkdir(parents=True, exist_ok=True)
    for name in ("piri-strokes-mask.png", "piri-silhouette-mask.png"):
        variants[name].save(SHELL_RESOURCES / name, optimize=True)

    print(f"Wrote {len(list(OUT.iterdir()))} files to {OUT.relative_to(ROOT)}, "
          f"plus the Plymouth theme images and the desktop's masks")


if __name__ == "__main__":
    main()
