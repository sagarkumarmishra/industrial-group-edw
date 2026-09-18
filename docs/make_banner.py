"""
Generates the README banner. Run once; the PNG is committed to the repo.

    python docs/make_banner.py

Kept in the repo rather than hand-made in a design tool so the banner can be
regenerated or restyled without hunting for the original file.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "assets", "banner.png")
W, H = 1280, 420

BG_TOP = (11, 18, 32)
BG_BOT = (18, 28, 46)
WHITE = (247, 250, 252)
GREY = (148, 163, 184)
CYAN = (34, 211, 238)
AMBER = (251, 191, 36)
GREEN = (52, 211, 153)
CARD = (23, 34, 54)

FONTS = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
FONTS_REG = [
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONTS_MONO = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\cour.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def font(paths: list[str], size: int):
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    img = Image.new("RGB", (W, H), BG_TOP)
    d = ImageDraw.Draw(img)

    # Vertical gradient background.
    for y in range(H):
        t = y / H
        d.line(
            [(0, y), (W, y)],
            fill=(
                int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
                int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
                int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t),
            ),
        )

    f_title = font(FONTS, 62)
    f_sub = font(FONTS_REG, 26)
    f_small = font(FONTS_REG, 19)
    f_mono = font(FONTS_MONO, 18)
    f_stat = font(FONTS, 34)
    f_lab = font(FONTS_REG, 15)

    # Accent bar.
    d.rounded_rectangle([60, 62, 68, 138], radius=4, fill=CYAN)

    d.text((90, 58), "INDUSTRIAL GROUP EDW", font=f_title, fill=WHITE)
    d.text((94, 128), "Six companies. Four systems. One source of truth.",
           font=f_sub, fill=GREY)

    # Pipeline flow.
    y0, bh, bw, gap = 208, 62, 214, 34
    stages = [
        ("4 SOURCE SYSTEMS", "ERP - legacy - workshop", CYAN),
        ("RAW", "land it, change nothing", GREY),
        ("STAGING", "fix units, zones, padding", AMBER),
        ("STAR SCHEMA", "6 dims - 4 facts", GREEN),
    ]
    x = 60
    for i, (title, sub, colour) in enumerate(stages):
        d.rounded_rectangle([x, y0, x + bw, y0 + bh], radius=10, fill=CARD)
        d.rounded_rectangle([x, y0, x + 5, y0 + bh], radius=3, fill=colour)
        d.text((x + 20, y0 + 13), title, font=f_small, fill=WHITE)
        d.text((x + 20, y0 + 38), sub, font=f_lab, fill=GREY)
        if i < len(stages) - 1:
            ax = x + bw + 9
            cy = y0 + bh // 2
            d.line([(ax, cy), (ax + gap - 18, cy)], fill=GREY, width=2)
            d.polygon(
                [(ax + gap - 18, cy - 5), (ax + gap - 18, cy + 5), (ax + gap - 8, cy)],
                fill=GREY,
            )
        x += bw + gap

    # Stats row.
    d.line([(60, 318), (W - 60, 318)], fill=(35, 48, 71), width=1)
    stats = [
        ("21", "dbt models", CYAN),
        ("70", "data tests", GREEN),
        ("9", "data traps", AMBER),
        ("4s", "full build", WHITE),
        ("0", "cost to run", GREY),
    ]
    sx = 60
    for value, label, colour in stats:
        d.text((sx, 340), value, font=f_stat, fill=colour)
        d.text((sx, 380), label, font=f_lab, fill=GREY)
        sx += 186

    d.text((W - 300, 356), "dbt - DuckDB - Snowflake", font=f_mono, fill=GREY)
    d.text((W - 300, 380), "Python - SQL", font=f_mono, fill=(90, 105, 130))

    img.save(OUT, "PNG", optimize=True)
    size_kb = os.path.getsize(OUT) / 1024
    print(f"Wrote {OUT}  ({W}x{H}, {size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
