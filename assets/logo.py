"""Render assets/logo.png from the same geometry as assets/logo.svg.

A dark shield ("Guard") with a green rim and a bright-green checkmark ("verified
safe to upgrade"), in BumpGuard's green palette. Deterministic, Pillow-only (no
SVG toolchain), with 4x supersampling for clean anti-aliased edges. Run from the
repo root:

    python assets/logo.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logo.png")

SIZE = 512
SS = 4  # supersample factor
S = SIZE * SS

GREEN_RIM = (63, 185, 80)     # #3fb950 shield rim
GREEN_TOP = (120, 230, 140)   # check gradient top
GREEN_BOT = (63, 185, 80)     # check gradient bottom
DARK_TOP = (28, 33, 40)       # #1c2128 shield body top
DARK_BOT = (13, 17, 23)       # #0d1117 shield body bottom
WHITE = (255, 255, 255)

# Glyph geometry in 512-space (sampled by the SVG too).
CHECK = [(184, 264), (238, 318), (352, 192)]
CHECK_W = 46


def cubic(p0, c0, c1, p1, n=60):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = (mt**3) * p0[0] + 3 * mt * mt * t * c0[0] + 3 * mt * t * t * c1[0] + t**3 * p1[0]
        y = (mt**3) * p0[1] + 3 * mt * mt * t * c0[1] + 3 * mt * t * t * c1[1] + t**3 * p1[1]
        pts.append((x, y))
    return pts


def shield_512():
    """Sample the shield outline in 512-space (matches the SVG path)."""
    p = [(256, 40), (432, 104), (432, 268)]
    p += cubic((432, 268), (432, 360), (352, 432), (256, 472))  # to bottom point
    p += cubic((256, 472), (160, 432), (80, 360), (80, 268))    # up left side
    p.append((80, 104))
    return p


def scale_pts(pts, s, cx=256.0, cy=270.0):
    return [(cx + s * (x - cx), cy + s * (y - cy)) for x, y in pts]


def to_canvas(pts):
    return [(x * SS, y * SS) for x, y in pts]


def shield_mask(scale=1.0):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).polygon(to_canvas(scale_pts(shield_512(), scale)), fill=255)
    return m


def vgrad(c0, c1):
    """Vertical gradient image, built fast via a 1-px-wide column resized."""
    col = Image.new("RGBA", (1, S))
    px = col.load()
    for y in range(S):
        t = y / (S - 1)
        px[0, y] = tuple(round(c0[i] + (c1[i] - c0[i]) * t) for i in range(3)) + (255,)
    return col.resize((S, S))


def fill(mask, c0, c1):
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(vgrad(c0, c1), (0, 0), mask)
    return out


def solid(mask, color):
    img = Image.new("RGBA", (S, S), color + (255,))
    img.putalpha(mask)
    return img


def drop_shadow(mask, blur, dy, alpha=120):
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh.paste(Image.new("RGBA", (S, S), (0, 0, 0, alpha)), (0, dy), mask)
    return sh.filter(ImageFilter.GaussianBlur(blur))


def sheen(body_mask, strength=70):
    hl = Image.new("L", (S, S), 0)
    ImageDraw.Draw(hl).ellipse([S * 0.10, -S * 0.10, S * 0.90, S * 0.42], fill=strength)
    hl = hl.filter(ImageFilter.GaussianBlur(S * 0.05))
    white = Image.new("RGBA", (S, S), (255, 255, 255, 255))
    white.putalpha(hl)
    return Image.composite(white, Image.new("RGBA", (S, S), (0, 0, 0, 0)), body_mask)


def glyph_mask(points_512, width_512):
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    pts = to_canvas(points_512)
    w = width_512 * SS
    r = w / 2
    for a, b in zip(pts[:-1], pts[1:]):
        d.line([a, b], fill=255, width=int(w))
    for x, y in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    return m


def build() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    rim = shield_mask(1.0)
    body = shield_mask(0.945)

    img.alpha_composite(drop_shadow(rim, S * 0.022, int(S * 0.014), alpha=120))
    img.alpha_composite(solid(rim, GREEN_RIM))
    img.alpha_composite(fill(body, DARK_TOP, DARK_BOT))
    img.alpha_composite(sheen(body))

    gm = glyph_mask(CHECK, CHECK_W)
    img.alpha_composite(drop_shadow(gm, S * 0.010, int(S * 0.006), alpha=80))
    img.alpha_composite(fill(gm, GREEN_TOP, GREEN_BOT))

    img.resize((SIZE, SIZE), Image.LANCZOS).save(OUT)
    print(f"wrote {OUT} ({SIZE}x{SIZE}, {os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    build()
