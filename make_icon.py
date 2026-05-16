#!/usr/bin/env python3
"""
Generates AppIcon.png (1024×1024) for SpotifyLyrics.app.
Requires Pillow:  pip install Pillow
"""

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
OUT  = Path(__file__).parent / "AppIcon.png"

BG      = (15,  15,  15)          # near-black
GREEN   = (29, 185, 84)           # Spotify green
WHITE   = (255, 255, 255)
OFFWHITE= (220, 220, 220)
DIM     = (100, 100, 100)


def rounded_rect(draw: ImageDraw.Draw, xy, radius: int, **kw):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, **kw)


def make_icon() -> Image.Image:
    img  = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Background rounded square ──────────────────────────────────────────
    pad = 40
    r   = 200
    rounded_rect(draw, [pad, pad, SIZE - pad, SIZE - pad], r, fill=(*BG, 255))

    # ── Left green accent bar ──────────────────────────────────────────────
    bar_w = 52
    bar_x = pad + 72
    bar_y0 = pad + 170
    bar_y1 = SIZE - pad - 170
    rounded_rect(draw, [bar_x, bar_y0, bar_x + bar_w, bar_y1], 26, fill=(*GREEN, 255))

    # ── Lyric text bars ───────────────────────────────────────────────────
    # Three horizontal bars representing lines of lyrics
    content_x = bar_x + bar_w + 64
    widths     = [0.58, 0.50, 0.38]          # as fraction of available width
    available  = SIZE - pad - 80 - content_x
    bar_h      = 52
    spacing    = 102
    start_y    = SIZE // 2 - spacing         # vertically centered

    for i, frac in enumerate(widths):
        bar_len = int(available * frac)
        y       = start_y + i * spacing
        alpha   = [255, 200, 130][i]
        rounded_rect(
            draw,
            [content_x, y, content_x + bar_len, y + bar_h],
            bar_h // 2,
            fill=(*OFFWHITE, alpha),
        )

    # ── Music note glyph (top-right corner accent) ─────────────────────────
    # Draw a simple filled circle + filled rectangle note
    note_cx = SIZE - pad - 140
    note_cy = pad + 160
    note_r  = 44

    # Note head (filled ellipse)
    draw.ellipse(
        [note_cx - note_r, note_cy - int(note_r * 0.75),
         note_cx + note_r, note_cy + int(note_r * 0.75)],
        fill=(*GREEN, 200),
    )
    # Note stem
    stem_w = 18
    stem_h = 110
    draw.rounded_rectangle(
        [note_cx + note_r - stem_w, note_cy - int(note_r * 0.75) - stem_h,
         note_cx + note_r,          note_cy - int(note_r * 0.75)],
        radius=9,
        fill=(*GREEN, 200),
    )
    # Note flag
    flag_pts = [
        (note_cx + note_r, note_cy - int(note_r * 0.75) - stem_h),
        (note_cx + note_r + 55, note_cy - int(note_r * 0.75) - stem_h + 36),
        (note_cx + note_r, note_cy - int(note_r * 0.75) - stem_h + 55),
    ]
    draw.polygon(flag_pts, fill=(*GREEN, 200))

    # ── Subtle inner shadow at edges ───────────────────────────────────────
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd     = ImageDraw.Draw(shadow)
    rounded_rect(sd, [pad, pad, SIZE - pad, SIZE - pad], r,
                 outline=(0, 0, 0, 90), width=30)
    img = Image.alpha_composite(img, shadow)

    return img


if __name__ == "__main__":
    icon = make_icon()
    icon.save(OUT)
    print(f"✓ Saved {OUT}")
