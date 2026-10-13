"""The Çatalburun dog drawn by hand (assets/dog/*.png, 68x68, transparent), loaded for the game.

Views: side (facing right), three-quarter front, three-quarter back (looking right and left), back.
Every view also gets a mirrored copy, so `views()["side_left"]` faces left. Animation strips added
later (e.g. assets/dog/sniff_1.png, sniff_2.png, ...) are found by name and loaded in order.

`python dog_assets.py <out.png> [scale]` writes a labelled sprite sheet of everything found.
"""

from __future__ import annotations

import glob
import os
import re
import sys

import pygame

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "dog")
VIEWS = {
    "side_right": "stand_side.png",
    "front_three_quarter_right": "stand_front_three_quarter.png",
    "back_three_quarter_right": "stand_back_three_quarter_right.png",
    "back_three_quarter_left": "stand_back_three_quarter_left.png",
    "back": "stand_back.png",
}


def load(name: str, folder: str = ASSET_DIR) -> pygame.Surface:
    image = pygame.image.load(os.path.join(folder, name))
    return image.convert_alpha() if pygame.display.get_surface() else image


def views(folder: str = ASSET_DIR) -> dict[str, pygame.Surface]:
    found = {key: load(name, folder) for key, name in VIEWS.items() if os.path.exists(os.path.join(folder, name))}
    for key in list(found):
        if key.endswith("_right") and key.replace("_right", "_left") not in found:
            found[key.replace("_right", "_left")] = pygame.transform.flip(found[key], True, False)
    return found


def animations(folder: str = ASSET_DIR) -> dict[str, list[pygame.Surface]]:
    """'<name>_<n>.png' files as numbered frames, e.g. sniff_1.png ... sniff_5.png -> {'sniff_right': [...]}.
    Frames are drawn facing right; the left-facing strip is mirrored."""
    strips: dict[str, list[tuple[int, str]]] = {}
    for path in glob.glob(os.path.join(folder, "*_[0-9]*.png")):
        match = re.match(r"^(.+)_(\d+)\.png$", os.path.basename(path))
        if match:
            strips.setdefault(match.group(1), []).append((int(match.group(2)), os.path.basename(path)))
    result = {}
    for name, files in strips.items():
        right = [load(f, folder) for _, f in sorted(files)]
        result[f"{name}_right"] = right
        result[f"{name}_left"] = [pygame.transform.flip(f, True, False) for f in right]
    return result


def sheet(scale: int = 4) -> pygame.Surface:
    """Every view and animation on one labelled sheet (for looking at, not for the game)."""
    rows = [("views", list(views().items()))] + [(k, [(f"{k} {i + 1}", f) for i, f in enumerate(v)])
                                                 for k, v in sorted(animations().items())]
    font = pygame.font.Font(None, 10 * scale // 2)
    cell = 68
    width = max(len(items) for _, items in rows) * (cell + 4) + 4
    height = len(rows) * (cell + 16) + 4
    image = pygame.Surface((width * scale, height * scale))
    image.fill((40, 44, 40))
    y = 4
    for _title, items in rows:
        x = 4
        for label, frame in items:
            pygame.draw.rect(image, (126, 168, 98), ((x * scale, y * scale), (cell * scale, cell * scale)))
            image.blit(pygame.transform.scale(frame, (frame.get_width() * scale, frame.get_height() * scale)),
                       (x * scale, y * scale))
            image.blit(font.render(label.replace("_", " "), True, (230, 230, 220)), (x * scale, (y + cell + 2) * scale))
            x += cell + 4
        y += cell + 16
    return image


if __name__ == "__main__":
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    pygame.display.set_mode((1, 1))
    pygame.image.save(sheet(int(sys.argv[2]) if len(sys.argv) > 2 else 4), sys.argv[1])
