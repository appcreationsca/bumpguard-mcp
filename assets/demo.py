"""Generate assets/demo.gif — an animated terminal demo of BumpGuard's
`check_upgrade` flagging the one breaking change (pydantic BaseSettings) in
your code before an upgrade.

Deterministic: renders frames with Pillow + a monospace font (no terminal
recording). Run from the repo root:  python assets/demo.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "demo.gif")

# ---- palette (GitHub dark) ---------------------------------------------------
BG = (13, 17, 23)
BAR = (22, 27, 34)
FG = (201, 209, 217)
DIM = (110, 118, 129)
CYAN = (88, 166, 255)
RED = (248, 81, 73)
GREEN = (63, 185, 80)
WHITE = (240, 246, 252)
YELLOW = (227, 179, 65)
DOT_R, DOT_Y, DOT_G = (255, 95, 86), (255, 189, 46), (39, 201, 63)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\CascadiaMono.ttf",
    r"C:\Windows\Fonts\CascadiaCode.ttf",
    r"C:\Windows\Fonts\consola.ttf",
]
SIZE = 22
LINE_H = 31
PAD = 26
BAR_H = 40


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


FONT = load_font(SIZE)

# Each line is a list of (text, color) segments. None = blank spacer line.
LINES: list[list[tuple[str, tuple[int, int, int]]]] = [
    [("# agent: ", DIM), ("before I upgrade ", FG), ("pydantic 1 -> 2", WHITE),
     (", will my code break?", FG)],
    None,
    [("> ", GREEN), ("bumpguard ", CYAN), ("check_upgrade", CYAN)],
    [("    package=", DIM), ('"pydantic"', FG), ("  from=", DIM), ('"1.10"', FG),
     ("  to=", DIM), ('"2.0"', FG)],
    None,
    [("  scanned ", DIM), ("2,015", WHITE),
     (" breaking API changes; matched against your code...", DIM)],
    None,
    [("  [X] NOT SAFE TO UPGRADE", RED), ("   - 1 breaking finding", RED)],
    None,
    [("  line 2  ", DIM), ("BREAKING", RED)],
    [("    pydantic.BaseSettings", WHITE), ("  ->  removed in 2.0", RED)],
    [("    fix: ", GREEN), ("from pydantic_settings import BaseSettings", FG)],
    None,
    [("  => BumpGuard flags the ", DIM), ("one", WHITE),
     (" change that hits ", DIM), ("YOUR", WHITE), (" code.", DIM)],
]

# The first line is "typed" word-by-word; the rest reveal one line per frame.
TYPE_LINE = 0


def line_text(line) -> str:
    return "" if line is None else "".join(t for t, _ in line)


def measure(text: str) -> int:
    return int(FONT.getlength(text))


MAX_W = max(measure(line_text(l)) for l in LINES)
W = MAX_W + PAD * 2
H = BAR_H + PAD + LINE_H * len(LINES) + PAD


def draw_segments(d: ImageDraw.ImageDraw, x: int, y: int, segments) -> None:
    for text, color in segments:
        d.text((x, y), text, font=FONT, fill=color)
        x += measure(text)


def render(num_lines: int, typed_words: int | None = None,
           cursor: bool = False) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # title bar
    d.rectangle([0, 0, W, BAR_H], fill=BAR)
    cy = BAR_H // 2
    for i, c in enumerate((DOT_R, DOT_Y, DOT_G)):
        cx = PAD + i * 22
        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=c)
    d.text((PAD + 80, cy - SIZE // 2), "bumpguard - check_upgrade",
           font=FONT, fill=DIM)

    y = BAR_H + PAD
    for idx in range(num_lines):
        line = LINES[idx]
        if line is not None:
            if idx == TYPE_LINE and typed_words is not None:
                words = line_text(line).split(" ")
                shown = " ".join(words[:typed_words])
                d.text((PAD, y), shown, font=FONT, fill=FG)
                if cursor:
                    cx = PAD + measure(shown + " ")
                    d.rectangle([cx, y + 2, cx + 11, y + SIZE], fill=FG)
            else:
                draw_segments(d, PAD, y, line)
        y += LINE_H
    return img


def build() -> None:
    frames: list[Image.Image] = []
    durations: list[int] = []

    # Phase 1 — type the agent question word by word.
    word_count = len(line_text(LINES[TYPE_LINE]).split(" "))
    for w in range(1, word_count + 1):
        frames.append(render(1, typed_words=w, cursor=True))
        durations.append(110)
    frames.append(render(1, typed_words=word_count, cursor=False))
    durations.append(350)

    # Phase 2 — reveal the rest, one line per frame, with emphasis pauses.
    emphasis = {7, 9, 10, 11, 13}  # NOT SAFE, finding block, tagline
    for n in range(2, len(LINES) + 1):
        frames.append(render(n))
        last = n - 1
        durations.append(520 if last in emphasis else 240)

    # Phase 3 — hold the final frame, then loop.
    frames.append(render(len(LINES)))
    durations.append(2600)

    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=64) for f in frames]
    pal[0].save(
        OUT, save_all=True, append_images=pal[1:], duration=durations,
        loop=0, optimize=True, disposal=2,
    )
    kb = os.path.getsize(OUT) / 1024
    print(f"wrote {OUT}  ({W}x{H}, {len(frames)} frames, {kb:.0f} KB)")


if __name__ == "__main__":
    build()
