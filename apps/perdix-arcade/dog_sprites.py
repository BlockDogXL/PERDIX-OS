"""Tarsus Çatalburun (Turkish pointer) pixel art for PERDIX Arcade.

Drawn from reference photos: white body with grey ticking and dark liver patches (one behind the
withers, one at the root of the tail), dark liver head with tan brows and cheeks, a narrow white
blaze, very long hanging ears, the split ("forked") nose with its cleft, a deep chest, long thin
legs, a thin tail carried straight back, and a red collar.

How it is drawn: the side-view body is a small rig (torso, neck, legs and tail as polygons and
thick segments with joint angles), rasterised straight onto a pixel grid, so every pose stays crisp
(nothing is rotated as an image). Shading (light from the top left), ticking, the patches and the
outline are pixel passes over that grid. The head and the front view are drawn by hand, pixel by
pixel, because that is where the character is: the eye, the brow and the forked nose.

Every frame faces right; `frames()` adds the mirrored left-facing ones.
"""

from __future__ import annotations

import math

import pygame

PALETTE = {
    "w": (236, 232, 222), "v": (252, 250, 244), "W": (196, 190, 178), "X": (160, 152, 142),  # whites
    "x": (128, 118, 112),                                      # grey ticking
    "l": (92, 70, 64), "h": (126, 100, 92), "L": (62, 46, 44), "D": (44, 32, 30),  # liver
    "t": (186, 130, 82), "T": (150, 98, 58),                   # tan points
    "n": (132, 88, 88), "N": (170, 120, 116), "g": (52, 32, 34),  # nose, highlight, cleft
    "e": (22, 14, 12), "a": (164, 104, 44), "E": (255, 255, 255), "r": (176, 92, 86),  # eye, iris, glint, lid
    "m": (46, 28, 26), "p": (220, 110, 118), "P": (180, 70, 82), "s": (246, 244, 236),  # mouth, tongue, teeth
    "c": (190, 40, 46), "C": (130, 24, 32), "o": (214, 176, 70),  # collar, its shade, ring
    "k": (24, 16, 14),                                         # outline
}
SHADE = {"w": "W", "v": "w", "l": "L", "h": "l", "t": "T", "L": "D", "W": "X", "c": "C"}
LIGHT = {"w": "v", "l": "h", "W": "w", "L": "l", "t": "t", "c": "c"}

FRAME_W, FRAME_H = 72, 46
GROUND = 43  # paws stand on this row

# --- hand-drawn heads (facing right, no outline: the outline pass adds it) --------------------------

HEAD_LEVEL = [
    ".....hhhhh..........",
    "...hhllllhhw........",
    "..hlllllllllww......",
    "..lllllllltlwwww....",
    ".lllllllllaewwwwwww.",
    ".llLLlllllllllwwwwnN",
    ".lLLLLlllltllllwwwng",
    ".LLLLLLllttllllllnnn",
    ".LLLLLLLltttwwwmmm..",
    "..LLLLLLLLtwwwwww...",
    "..LLLLLLLL..........",
    "...LLLLLLL..........",
    "...LLLLLL...........",
    "....LLLL............",
    ".....LL.............",
]
HEAD_LEVEL_ANCHOR = (3, 9)  # the pixel that sits on the top of the neck

# ear flying back (running, leaping)
HEAD_FLY = [
    ".....hhhhh..........",
    "...hhllllhhw........",
    ".LLlllllllllww......",
    "LLLLLllllltlwwww....",
    "LLLLLLlllllaewwwwwww",
    ".LLLLLLLllllllwwwwnN",
    "..LLLLLLllltlllwwwng",
    "....LLlLlttllllllnnn",
    ".....lllltttwwwmmm..",
    ".....llllltwwwwww...",
    "......lll...........",
]
HEAD_FLY_ANCHOR = (6, 9)

# nose down to the ground: sniffing, and diving into the grass
HEAD_DOWN = [
    "....hhhh.......",
    "..hhllllh......",
    ".hllllllll.....",
    ".lllllllllw....",
    "LLlllllltlww...",
    "LLLlllllaeww...",
    "LLLLlllllwww...",
    "LLLLlllltlwww..",
    ".LLLLlltttwww..",
    ".LLLLLlttllwww.",
    "..LLLLLltlllww.",
    "..LLLLLLlttlwww",
    "...LLLLL.ttllwm",
    "...LLLL...lllnm",
    "....LL.....nNnn",
    "............ngn",
]
HEAD_DOWN_ANCHOR = (2, 2)

# --- the front view (left half, mirrored; the centre columns make the nose cleft) ------------------------

FRONT_HALF = [
    ".........hhhhh",
    ".......hhllllw",
    "......hllllllw",
    ".....hllllllww",
    "....LllltTllww",
    "...LLlllttllww",
    "..LLLlleaallww",
    "..LLLlleEallww",
    ".LLLLllrrlllww",
    ".LLLLlltllllww",
    ".LLLLLttlllwww",
    "LLLLLLttlllwww",
    "LLLLLLLttllwww",
    "LLLLLLLttlwwww",
    "LLLLLLLLtwwnnn",
    "LLLLLLLLwwnNng",
    "LLLLLLLLwwnnng",
    "LLLLLLLLLwwnnn",
    ".LLLLLLLLwwwwg",
    ".LLLLLLLLwxwwm",
    ".LLLLLLL.wwwwm",
    "..LLLLLL.Wwmmm",
    "..LLLLLL.WWwww",
    "...LLLL..ccccc",
    "...LLL..wccocc",
    "....L..wwwwwww",
    ".......wwxwwww",
]
FRONT_EARS_UP = {  # alert: the ears lift at their base
    3: "..L..hllllllww",
    4: ".LLL.llltTllww",
    5: ".LLLLlllttllww",
}
FRONT_MOUTH = {
    "plain": {},
    "happy": {20: ".LLLLLLL.wwwmm", 21: "..LLLLLL.Wmppp", 22: "..LLLLLL.WWmpP"},
    "laugh": {19: ".LLLLLLLLwmmmm", 20: ".LLLLLLL.wmsss", 21: "..LLLLLL.Wmppp", 22: "..LLLLLL.WWmpp",
              23: "...LLLL..ccmmm"},
}


def mirrored(half: list[str]) -> list[str]:
    return [row + row[::-1] for row in half]


def front(mouth: str = "plain", ears_up: bool = False) -> list[str]:
    rows = list(FRONT_HALF)
    if ears_up:
        rows = [FRONT_EARS_UP.get(i, r) for i, r in enumerate(rows)]
    rows = [FRONT_MOUTH[mouth].get(i, r) for i, r in enumerate(rows)]
    return mirrored(rows)


# --- the side-view rig ----------------------------------------------------------------------------------------

# Body space: origin at the hip joint, x forward (towards the head), y down.
TORSO = [(-6, -4), (-4, -7), (2, -8), (12, -8), (20, -9), (25, -8), (28, -5), (29, -1), (28, 3), (25, 6),
         (20, 7), (14, 4), (8, 2), (2, 3), (-3, 2), (-6, 0)]
NECK = [(21, -8), (25, -14), (28, -17), (32, -16), (31, -9), (29, -2), (26, -4)]
NECK_TOP = (29, -16)  # where the head goes
PATCHES = [  # dark liver patches, from the reference: behind the withers and at the root of the tail
    [(13, -9), (21, -10), (23, -5), (20, -1), (15, -2), (12, -5)],
    [(-7, -6), (0, -9), (3, -5), (0, -2), (-5, -2)],
    [(23, 1), (26, 1), (26, 4), (23, 4)],  # spot at the elbow
]
TICKING = [(4, -2), (7, 0), (9, -4), (3, -6), (17, 2), (19, 4), (11, 1), (24, -3), (6, -6), (26, -1), (1, 0),
           (14, 1), (22, 5), (8, -7), (-2, -1)]
REAR_HIP, FRONT_SHOULDER = (0, -1), (24, -2)
REAR_LEG = ((9, 5.0, 4.2), (8, 3.2, 2.6), (6, 2.4, 2.2))  # (length, width at top, width at bottom)
FRONT_LEG = ((8, 4.4, 3.2), (9, 2.8, 2.4), (3, 2.2, 2.2))
TAIL_BASE = (-6, -4)
TAIL = ((6, 2.6, 2.2), (6, 2.2, 1.6), (5, 1.6, 1.2))


class Pose:
    """Joint angles in degrees: 0 points straight down, positive turns towards the head."""

    def __init__(self, body=0.0, x=14.0, y=None, rear=(25, -35, 0), rear_far=None, front=(-20, 0, 12),
                 front_far=None, tail=(-80, -5, 0), head="level", neck=(0, 0)):
        self.body, self.x = body, x
        self.y = y
        self.rear, self.front = rear, front
        self.rear_far = rear_far if rear_far is not None else rear
        self.front_far = front_far if front_far is not None else front
        self.tail, self.head, self.neck = tail, head, neck


class Grid:
    def __init__(self, w: int = FRAME_W, h: int = FRAME_H) -> None:
        self.w, self.h = w, h
        self.color = [[None] * w for _ in range(h)]
        self.part = [[-1] * w for _ in range(h)]

    def set(self, x: int, y: int, key: str, part: int) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.color[y][x] = key
            self.part[y][x] = part

    def get(self, x: int, y: int):
        return self.color[y][x] if 0 <= x < self.w and 0 <= y < self.h else None

    def part_at(self, x: int, y: int) -> int:
        return self.part[y][x] if 0 <= x < self.w and 0 <= y < self.h else -1


def inside(polygon, px: float, py: float) -> bool:
    result = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def fill_polygon(grid: Grid, polygon, key: str, part: int, clip_part: int | None = None) -> None:
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    for y in range(max(0, int(min(ys)) - 1), min(grid.h, int(max(ys)) + 2)):
        for x in range(max(0, int(min(xs)) - 1), min(grid.w, int(max(xs)) + 2)):
            if inside(polygon, x + 0.5, y + 0.5) and (clip_part is None or grid.part_at(x, y) == clip_part):
                grid.set(x, y, key, part if clip_part is None else clip_part)


def segment(grid: Grid, a, b, w0: float, w1: float, key: str, part: int) -> None:
    """A tapered capsule from a (width w0) to b (width w1)."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy or 1e-9
    r = max(w0, w1) / 2 + 1
    for y in range(int(min(ay, by) - r), int(max(ay, by) + r) + 1):
        for x in range(int(min(ax, bx) - r), int(max(ax, bx) + r) + 1):
            cx, cy = x + 0.5, y + 0.5
            t = max(0.0, min(1.0, ((cx - ax) * dx + (cy - ay) * dy) / length2))
            px, py = ax + t * dx, ay + t * dy
            if math.hypot(cx - px, cy - py) <= (w0 + (w1 - w0) * t) / 2:
                grid.set(x, y, key, part)


def stamp(grid: Grid, rows: list[str], x0: int, y0: int, part: int) -> None:
    for y, row in enumerate(rows):
        for x, key in enumerate(row):
            if key != ".":
                grid.set(x0 + x, y0 + y, key, part)


class Rig:
    def __init__(self, pose: Pose) -> None:
        self.pose = pose
        a = math.radians(pose.body)  # positive: the front end goes up
        self.cos, self.sin = math.cos(a), math.sin(a)
        hip_y = pose.y if pose.y is not None else GROUND - 21
        self.ox, self.oy = pose.x, hip_y

    def at(self, point):
        x, y = point
        return (self.ox + x * self.cos + y * self.sin, self.oy - x * self.sin + y * self.cos)

    def limb(self, start, angles, segments, offset=0.0):
        """Joint positions for a chain; angles are relative to straight down, turned with the body."""
        points = [self.at(start)]
        x, y = points[0]
        for angle, (length, _w0, _w1) in zip(angles, segments):
            a = math.radians(angle - self.pose.body + offset)
            x, y = x + math.sin(a) * length, y + math.cos(a) * length
            points.append((x, y))
        return points


def draw_chain(grid: Grid, points, segments, key: str, part: int) -> None:
    for (a, b), (_length, w0, w1) in zip(zip(points, points[1:]), segments):
        segment(grid, a, b, w0, w1, key, part)


def paw(grid: Grid, point, facing_down: bool, key: str, part: int) -> None:
    x, y = int(point[0]), int(point[1])
    for dx in (-1, 0, 1, 2):
        grid.set(x + dx, y, key, part)
    grid.set(x, y - 1, key, part)
    grid.set(x + 1, y - 1, key, part)


# parts (draw order = z): far legs, tail, torso, neck, near legs, head
FAR, TAILP, TORSOP, NECKP, NEAR, HEADP = range(6)


def render(pose: Pose) -> Grid:
    grid = Grid()
    rig = Rig(pose)
    # far legs, a shade darker
    rear_far = rig.limb(REAR_HIP, pose.rear_far, REAR_LEG)
    front_far = rig.limb(FRONT_SHOULDER, pose.front_far, FRONT_LEG)
    draw_chain(grid, rear_far, REAR_LEG, "W", FAR)
    draw_chain(grid, front_far, FRONT_LEG, "W", FAR)
    paw(grid, rear_far[-1], True, "W", FAR)
    paw(grid, front_far[-1], True, "W", FAR)
    # tail: thin, dark at the root, white tip
    tail = rig.limb(TAIL_BASE, pose.tail, TAIL)
    draw_chain(grid, tail[:2], TAIL[:1], "l", TAILP)
    draw_chain(grid, tail[1:], TAIL[1:], "w", TAILP)
    # torso, neck, patches, ticking
    fill_polygon(grid, [rig.at(p) for p in TORSO], "w", TORSOP)
    neck = [(x + pose.neck[0] * (i in (1, 2, 3, 4)), y + pose.neck[1] * (i in (1, 2, 3, 4)))
            for i, (x, y) in enumerate(NECK)]
    fill_polygon(grid, [rig.at(p) for p in neck], "w", NECKP)
    for patch in PATCHES:
        fill_polygon(grid, [rig.at(p) for p in patch], "l", TORSOP, clip_part=TORSOP)
    for p in TICKING:
        x, y = rig.at(p)
        if grid.get(int(x), int(y)) == "w":
            grid.set(int(x), int(y), "x", TORSOP)
    # the neck's dark top (the head colour runs down the back of the neck), and the collar
    for i in range(2):
        x, y = rig.at((NECK_TOP[0] - 2 - i * 2 + pose.neck[0], NECK_TOP[1] + 1 + i * 2 + pose.neck[1]))
        for dx in range(-2, 2):
            if grid.part_at(int(x) + dx, int(y)) == NECKP:
                grid.set(int(x) + dx, int(y), "l", NECKP)
    collar = [rig.at((26 + pose.neck[0] * 0.6 + k * 0.6, -12 + pose.neck[1] * 0.6 + k)) for k in range(6)]
    for x, y in collar:
        for dx in (0, 1):
            if grid.part_at(int(x) + dx, int(y)) == NECKP:
                grid.set(int(x) + dx, int(y), "c", NECKP)
    # near legs, ticked
    rear = rig.limb(REAR_HIP, pose.rear, REAR_LEG)
    front = rig.limb(FRONT_SHOULDER, pose.front, FRONT_LEG)
    draw_chain(grid, rear, REAR_LEG, "w", NEAR)
    draw_chain(grid, front, FRONT_LEG, "w", NEAR)
    paw(grid, rear[-1], True, "w", NEAR)
    paw(grid, front[-1], True, "w", NEAR)
    for chain in (rear, front):
        mid = chain[1]
        lower = ((chain[1][0] + chain[2][0]) / 2, (chain[1][1] + chain[2][1]) / 2)
        for x, y in (mid, lower):
            if grid.get(int(x), int(y) + 1) == "w":
                grid.set(int(x), int(y) + 1, "x", NEAR)
    # head on the top of the neck
    rows, anchor = {"level": (HEAD_LEVEL, HEAD_LEVEL_ANCHOR), "fly": (HEAD_FLY, HEAD_FLY_ANCHOR),
                    "down": (HEAD_DOWN, HEAD_DOWN_ANCHOR)}[pose.head]
    hx, hy = rig.at((NECK_TOP[0] + pose.neck[0], NECK_TOP[1] + pose.neck[1]))
    stamp(grid, rows, int(round(hx)) - anchor[0], int(round(hy)) - anchor[1], HEADP)
    return finish(grid)


def finish(grid: Grid) -> Grid:
    """Light from the top left, a dark line where a nearer part overlaps a farther one, the outline."""
    src = [row[:] for row in grid.color]
    for y in range(grid.h):
        for x in range(grid.w):
            key = src[y][x]
            if key is None:
                continue
            part = grid.part[y][x]
            below = src[y + 1][x] if y + 1 < grid.h else None
            above = src[y - 1][x] if y > 0 else None
            right = src[y][x + 1] if x + 1 < grid.w else None
            if (below is None or right is None) and key in SHADE:
                grid.color[y][x] = SHADE[key]
            elif above is None and key in LIGHT:
                grid.color[y][x] = LIGHT[key]
            # a nearer part's edge over a farther part: darken the farther pixel
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < grid.w and 0 <= ny < grid.h and src[ny][nx] is not None:
                    if grid.part[ny][nx] > part + 0 and grid.part[ny][nx] in (NEAR, HEADP) and part in (
                            TORSOP, NECKP, FAR, TAILP):
                        grid.color[y][x] = {"w": "X", "W": "X", "x": "X", "l": "D", "L": "D", "c": "C",
                                            "v": "W"}.get(key, key)
    outline(grid)
    return grid


def outline(grid: Grid) -> None:
    src = [row[:] for row in grid.color]
    for y in range(grid.h):
        for x in range(grid.w):
            if src[y][x] is None and any(
                    0 <= nx < grid.w and 0 <= ny < grid.h and src[ny][nx] is not None and src[ny][nx] != "k"
                    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))):
                grid.color[y][x] = "k"


def from_rows(rows: list[str], pad: int = 1) -> Grid:
    grid = Grid(max(len(r) for r in rows) + pad * 2, len(rows) + pad * 2)
    stamp(grid, rows, pad, pad, HEADP)
    outline(grid)
    return grid


def to_surface(grid: Grid) -> pygame.Surface:
    image = pygame.Surface((grid.w, grid.h), pygame.SRCALPHA)
    for y in range(grid.h):
        for x in range(grid.w):
            key = grid.color[y][x]
            if key is not None:
                image.set_at((x, y), PALETTE[key])
    return image


# --- the animations ----------------------------------------------------------------------------------------

def poses() -> dict[str, list[Pose]]:
    stand = dict(rear=(25, -35, 0), front=(-20, 0, 12), rear_far=(18, -32, 2), front_far=(-14, 2, 12))
    return {
        # standing, tail wagging
        "idle": [Pose(tail=(-78, -12, -20), **stand), Pose(tail=(-70, -2, 10), **stand)],
        # trotting / running: four frames of a gallop
        "run": [
            Pose(rear=(55, 0, 10), rear_far=(40, -5, 10), front=(-60, -40, -20), front_far=(-45, -30, -10),
                 tail=(-85, -10, 0), head="fly", y=GROUND - 22),
            Pose(rear=(20, -20, 0), rear_far=(5, -30, 0), front=(-5, 15, 30), front_far=(-15, 5, 20),
                 tail=(-80, -5, 0), head="fly", y=GROUND - 21),
            Pose(rear=(-10, -55, -30), rear_far=(-5, -45, -20), front=(40, 55, 70), front_far=(30, 40, 60),
                 tail=(-75, 0, 5), head="fly", y=GROUND - 22, body=3),
            Pose(rear=(10, -40, -10), rear_far=(25, -30, 0), front=(10, 25, 40), front_far=(-10, 10, 20),
                 tail=(-82, -8, 0), head="fly", y=GROUND - 21),
        ],
        # leaping into the grass: crouch, push off, flight, dive, gone (only the tail shows)
        "leap": [
            Pose(rear=(70, -70, -10), rear_far=(65, -65, -10), front=(-50, 10, 20), front_far=(-40, 10, 20),
                 tail=(-60, -10, -10), y=GROUND - 16, body=-6),
            Pose(rear=(-40, -60, -70), rear_far=(-35, -55, -60), front=(60, 80, 90), front_far=(50, 70, 80),
                 tail=(-100, -10, 0), head="fly", y=GROUND - 24, body=22),
            Pose(rear=(-60, -80, -90), rear_far=(-55, -75, -85), front=(80, 95, 100), front_far=(70, 90, 95),
                 tail=(-100, -15, -5), head="fly", y=GROUND - 32, x=12, body=8),
            Pose(rear=(-80, -100, -110), rear_far=(-70, -95, -100), front=(30, 40, 30), front_far=(20, 30, 20),
                 tail=(-110, -20, -10), head="down", y=GROUND - 28, x=16, body=-30),
            Pose(rear=(-80, -100, -110), rear_far=(-70, -95, -100), front=(10, 20, 20), front_far=(0, 10, 10),
                 tail=(-150, -30, -10), head="down", y=GROUND - 16, x=22, body=-55),
        ],
        # finding the bird: nose down, then the classic point (front paw up, tail straight back)
        "point": [
            Pose(rear=(30, -30, 0), rear_far=(20, -30, 0), front=(-10, 10, 15), front_far=(-25, 0, 10),
                 tail=(-75, 0, 0), head="down", neck=(2, 6), y=GROUND - 20, body=-4),
            Pose(rear=(35, -35, 0), rear_far=(28, -32, 0), front=(-40, 60, 70), front_far=(-15, 0, 10),
                 tail=(-88, 0, 0), head="level", neck=(2, 1), y=GROUND - 20, body=-2),
            Pose(rear=(40, -35, 0), rear_far=(30, -32, 0), front=(-40, 65, 75), front_far=(-15, 0, 10),
                 tail=(-90, 0, 0), head="level", neck=(3, 2), y=GROUND - 19, body=-4),
        ],
    }


def frames() -> dict[str, list[pygame.Surface]]:
    """Every animation as surfaces at 1x: '<name>_right' and '<name>_left', plus the front views."""
    result = {}
    for name, sequence in poses().items():
        right = [to_surface(render(p)) for p in sequence]
        result[f"{name}_right"] = right
        result[f"{name}_left"] = [pygame.transform.flip(f, True, False) for f in right]
    result["front"] = [to_surface(from_rows(front(m, up))) for m, up in
                       (("plain", False), ("plain", True), ("happy", True), ("laugh", False))]
    return result


def check_maps() -> None:
    for name, rows in (("HEAD_LEVEL", HEAD_LEVEL), ("HEAD_FLY", HEAD_FLY), ("HEAD_DOWN", HEAD_DOWN),
                       ("FRONT_HALF", FRONT_HALF)):
        widths = {len(r) for r in rows}
        if len(widths) != 1:
            raise ValueError(f"{name}: rows of different widths {sorted(widths)}")
    for name, rows in list(FRONT_EARS_UP.items()) + [(k, r) for m in FRONT_MOUTH.values() for k, r in m.items()]:
        if len(rows) != len(FRONT_HALF[0]):
            raise ValueError(f"front row {name} is {len(rows)} wide")
