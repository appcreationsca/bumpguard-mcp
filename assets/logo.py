"""Render assets/logo.png from the same geometry as assets/logo.svg.

A shield ("Guard") containing an upward arrow ("Bump" = version upgrade), in
BumpGuard's green palette. Deterministic, Pillow-only (no SVG toolchain), with
4x supersampling for clean anti-aliased edges. Run from the repo root:

    python assets/logo.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logo.png")

SIZE = 512
SS = 4  # supersample factor
S = SIZE * SS

GREEN_TOP = (63, 185, 80)
GREEN_BOT = (26, 127, 55)
OUTLINE = (13, 17, 23)
WHITE = (255, 255, 255)


def cubic(p0, c0, c1, p1, n=60):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = (mt**3) * p0[0] + 3 * mt * mt * t * c0[0] + 3 * mt * t * t * c1[0] + t**3 * p1[0]
        y = (mt**3) * p0[1] + 3 * mt * mt * t * c0[1] + 3 * mt * t * t * c1[1] + t**3 * p1[1]
        pts.append((x, y))
    return pts


def shield_points() -> list[tuple[float, float]]:
    """Sample the shield outline (matches the SVG path)."""
    p = []
    p.append((256, 40))          # top apex
    p.append((432, 104))         # top-right
    p.append((432, 268))         # right, before curve
    p += cubic((432, 268), (432, 360), (352, 432), (256, 472))  # to bottom point
    p += cubic((256, 472), (160, 432), (80, 360), (80, 268))    # up left side
    p.append((80, 104))          # left top
    return [(x * SS, y * SS) for x, y in p]


def arrow_points() -> list[tuple[float, float]]:
    pts = [(256, 152), (360, 272), (300, 272), (300, 364), (212, 364), (212, 272), (152, 272)]
    return [(x * SS, y * SS) for x, y in pts]


def build() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Vertical green gradient, masked to the shield shape.
    grad = Image.new("RGBA", (S, S))
    gpx = grad.load()
    for y in range(S):
        t = y / (S - 1)
        r = round(GREEN_TOP[0] + (GREEN_BOT[0] - GREEN_TOP[0]) * t)
        g = round(GREEN_TOP[1] + (GREEN_BOT[1] - GREEN_TOP[1]) * t)
        b = round(GREEN_TOP[2] + (GREEN_BOT[2] - GREEN_TOP[2]) * t)
        for x in range(S):
            gpx[x, y] = (r, g, b, 255)

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(shield_points(), fill=255)
    img.paste(grad, (0, 0), mask)

    # Dark outline + white arrow.
    d = ImageDraw.Draw(img)
    d.line(shield_points() + [shield_points()[0]], fill=OUTLINE, width=12 * SS, joint="curve")
    d.polygon(arrow_points(), fill=WHITE)

    img.resize((SIZE, SIZE), Image.LANCZOS).save(OUT)
    print(f"wrote {OUT} ({SIZE}x{SIZE}, {os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    build()
