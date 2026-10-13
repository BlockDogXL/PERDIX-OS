"""Partridge Hunt pixel art, drawn in code: every sprite is a small map of characters, one per pixel,
turned into a pygame Surface (no image files needed). '.' is transparent.

The partridge is the grey partridge (çil keklik, Perdix perdix): grey body, orange-rust face,
chestnut bars on the flanks, a dark chestnut horseshoe on the belly, rufous tail.
The dog is a Turkish pointer (Çatalburun): white with liver patches and ticking, liver head with a
white blaze, long drooping ears and its famous split ("forked") nose.
"""

from __future__ import annotations

import pygame

PARTRIDGE = {
    "k": (44, 36, 32), "g": (140, 142, 142), "G": (182, 184, 180), "d": (100, 102, 104),
    "o": (210, 116, 50), "O": (236, 156, 84), "c": (130, 60, 34), "b": (124, 98, 72),
    "B": (160, 130, 96), "e": (14, 12, 10), "y": (206, 196, 176), "r": (176, 84, 42),
    "h": (98, 42, 26), "w": (250, 250, 250),
}

DOG = {
    "k": (38, 24, 18), "w": (238, 234, 226), "W": (196, 188, 176), "l": (124, 66, 36),
    "L": (88, 44, 24), "t": (150, 92, 62), "n": (54, 28, 22), "N": (110, 60, 48),
    "p": (228, 104, 112), "e": (22, 14, 10), "E": (250, 250, 250), "m": (40, 18, 14),
    "s": (250, 248, 240),
}

# --- the partridge (22 x 14, facing right) -----------------------------------------------------------

BIRD_BODY = [
    "......................",
    "......................",
    "...............kkkk...",
    "..............kOOOOk..",
    "....kkkkkkkkkkOOoeOyy.",
    "..kkBbBbBbBbBgkOOOOk..",
    ".krrbBbBbBbBggggOOk...",
    "krrrgggcgggcggggggk...",
    ".krggcgggcgggGGGGGk...",
    "..kgggggggghhhGGGk....",
    "...kkgggghhhhGGkk.....",
    ".....kkkkkkkkkk.......",
    "......................",
    "......................",
]

WING_UP = [
    ".......kkk............",
    "......kBbBk...........",
    ".....kBbBbBk..........",
    "....kBbBbBbk..........",
    "....kbBbBbBk..........",
    ".....kbbbbk...........",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
]

WING_MID = [
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "....kkkkkkk...........",
    "...kBbBbBbBk..........",
    "....kdBdBdBk..........",
    ".....kkkkkk...........",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
]

WING_DOWN = [
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......................",
    "......kbBbBk..........",
    "......kBbBbBk.........",
    ".......kbBbBk.........",
    "........kbBbk.........",
    ".........kkk..........",
    "......................",
]

# --- the dog ---------------------------------------------------------------------------------------------

# Front view, head and chest (26 x 24). Eyes are drawn by code so they can follow a bird.
DOG_FRONT = [
    ".........kkkkkkkk.........",
    ".......kkllllwwllllkk.....",
    "......kllllllwwlllllllk...",
    ".....klllllllwwllllllllk..",
    "....kLlllllllwwlllllllLLk.",
    "...kLLlllllllwwlllllllLLLk",
    "..kLLLllllllwwwwllllllLLLk",
    "..kLLLllllllwwwwlllllkLLLk",
    "..kLLLklllllwwwwlllllkLLLk",
    "..kLLLkllllwwwwwwllllkLLLk",
    "..kLLLLklllwwwwwwlllkLLLLk",
    "..kLLLLkllwwwwwwwwllkLLLLk",
    "...kLLLkkwwwwwwwwwwkkLLLk.",
    "...kLLLLkwwwnnNnnwwkLLLLk.",
    "....kLLLkwwnnnknnnwkLLLk..",
    "....kLLLkwwwnnknnwwkLLLk..",
    ".....kLLkkwwwwwwwwkkLLk...",
    "......kkkwkwwwwwwkwkkk....",
    "........kwwkkkkkkwwk......",
    ".......kwwwwwwwwwwwwk.....",
    "......kwwttwwwwwwwtwwk....",
    ".....kwwwwwwwtwwwwwwwwk...",
    ".....kwtwwwwwwwwwwtwwwk...",
    ".....kwwwwwwwwwwwwwwwwk...",
]
# where the eyes go (left eye, right eye) and the mouth rows that change
DOG_EYES = ((9, 9), (16, 9))

# ears lifted: the top of each ear pulled up beside the head (rows 2-7 replaced)
EARS_UP = {
    1: ".kk....kkllllwwllllkk..kk.",
    2: "kLLk..kllllllwwlllllllkLLk",
    3: "kLLLkklllllllwwllllllllkLk",
    4: "kLLLLlllllllwwwlllllllLLLk",
}

MOUTH_SMILE = {
    16: ".....kLLkkwwwwwwwwkkLLk...",
    17: "......kkkwkmmmmmmkwkkk....",
    18: "........kwwkpppkkwwk......",
}
MOUTH_LAUGH = {  # wide open: teeth, tongue
    15: "....kLLLksssssssssskLLLk..",
    16: ".....kLLkmmmmmmmmmmkLLk...",
    17: "......kkkmmmppppmmmkkk....",
    18: "........kkmppppmmkk.......",
}

# Side view running (34 x 20, facing right): two leg frames.
DOG_SIDE = [
    "..........................kkk.....",
    ".........................kLLLk....",
    "........................kLlllLk...",
    "........................kLlllllkk.",
    "........................kLlelllnnk",
    ".k......................kLLllwwwNk",
    "kwk....................kLLkllwwwk.",
    ".kwk...................kLLkkkkkk..",
    "..kwk....kkkkkkkkkkkkkkwLk........",
    "...kwkkkkwwwwwllllwwwwwwk.........",
    "....kwwwwwwwwlllllwwtwwwk.........",
    "....kwwtwwwwwwlllwwwwwwwk.........",
    "....kwwwwwwtwwwwwwwwtwwk..........",
    "....kWwwwwwwwwwwwwwwwwWk..........",
    ".....kWWkkkkkkkkkkkkWWk...........",
]
LEGS_A = [
    ".....kwk.............kwk..........",
    "....kwk...............kwk.........",
    "...kwk.................kwk........",
    "..kWk...................kWk.......",
    "..kk.....................kk.......",
]
LEGS_B = [
    "......kwk...........kwk...........",
    "......kwk...........kwk...........",
    ".....kwwk..........kwwk...........",
    ".....kWk...........kWk............",
    ".....kk............kk.............",
]


def surface(rows: list[str], palette: dict, scale: int = 1) -> pygame.Surface:
    width = max(len(r) for r in rows)
    image = pygame.Surface((width, len(rows)), pygame.SRCALPHA)
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char != "." and char in palette:
                image.set_at((x, y), palette[char])
    return pygame.transform.scale(image, (width * scale, len(rows) * scale)) if scale != 1 else image


def overlay(base: list[str], top: list[str]) -> list[str]:
    return ["".join(t if t != "." else b for b, t in zip(brow, trow)) for brow, trow in zip(base, top)]


def replaced(base: list[str], rows: dict[int, str]) -> list[str]:
    return [rows.get(i, row) for i, row in enumerate(base)]


def check(rows: list[str], name: str) -> None:
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise ValueError(f"{name}: rows of different widths {sorted(widths)}")


class Sprites:
    """All frames, built once."""

    def __init__(self) -> None:
        for name in ("BIRD_BODY", "WING_UP", "WING_MID", "WING_DOWN", "DOG_FRONT", "DOG_SIDE", "LEGS_A", "LEGS_B"):
            check(globals()[name], name)
        wings = [WING_UP, WING_MID, WING_DOWN, WING_MID]
        self.bird_right = [surface(overlay(BIRD_BODY, w), PARTRIDGE) for w in wings]
        self.bird_left = [pygame.transform.flip(f, True, False) for f in self.bird_right]
        self.bird_hit = surface(overlay(BIRD_BODY, WING_UP), PARTRIDGE)
        self.bird_fall = [pygame.transform.rotate(surface(overlay(BIRD_BODY, WING_UP), PARTRIDGE), angle)
                          for angle in (-90, -100, -80)]
        self.dog_front = {
            ("down", "smile"): surface(replaced(DOG_FRONT, MOUTH_SMILE), DOG),
            ("up", "plain"): surface(replaced(DOG_FRONT, EARS_UP), DOG),
            ("down", "plain"): surface(DOG_FRONT, DOG),
            ("up", "smile"): surface(replaced(replaced(DOG_FRONT, EARS_UP), MOUTH_SMILE), DOG),
            ("down", "laugh"): surface(replaced(DOG_FRONT, MOUTH_LAUGH), DOG),
        }
        side = [surface(DOG_SIDE + LEGS_A, DOG), surface(DOG_SIDE + LEGS_B, DOG)]
        self.dog_run_right = side
        self.dog_run_left = [pygame.transform.flip(f, True, False) for f in side]
