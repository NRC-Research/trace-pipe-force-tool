#!/usr/bin/env python3
"""Superimpose the computed VAL-005 wet-case CF203 on the manual's Figure 17.

Renders page 40 of the R5FORCE manual (report p. 33: CF203, new/Watkins
method, second sample problem, 0.2-0.5 s), calibrates its plot frame, and
draws the computed CF203 from VAL_005W.th on top in solid red, at true
scale on the figure's own axes - no shifting, no scaling. The positive
peak rises above the 1990 frame, honestly.

Frame calibration was measured from the 150-dpi render: pixel box
(448, 359)-(1516, 1052) maps to (0.2 s, +0.5E5 N)-(0.5 s, -1.0E5 N),
verified against the 0.0 gridline. If the render resolution changes, the
box scales with it (measured at DPI = 150).

Needs: pdftoppm (poppler), PIL. Run from anywhere:
    python3 test-validation/overlay_VAL_005W_fig17.py
"""

import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
PDF = os.path.join(HERE, os.pardir, "references", "r5force",
                   "R5FORCEMOD3-EGG-EAST-9232-6341078.pdf")
TH = os.path.join(HERE, "VAL_005W.th")
OUT = os.path.join(HERE, "VAL_005W_fig17_overlay.png")

DPI = 150
PAGE = 40  # PDF page holding report Figure 17 (p. 33)

# Frame calibration at DPI=150 (see module docstring).
X0_PX, X1_PX = 448.0, 1516.0     # t = 0.2 s ... 0.5 s
Y0_PX, Y1_PX = 359.0, 1052.0     # F = +0.5E5 N ... -1.0E5 N
T0, T1 = 0.2, 0.5
F0, F1 = +0.5e5, -1.0e5

def render_page():
    tmp = tempfile.mkdtemp()
    subprocess.run(["pdftoppm", "-f", str(PAGE), "-l", str(PAGE),
                    "-r", str(DPI), "-png", PDF,
                    os.path.join(tmp, "pg")], check=True)
    path = next(os.path.join(tmp, f) for f in sorted(os.listdir(tmp)))
    img = Image.open(path).convert("RGB")
    return img.rotate(-90, expand=True)  # scan is sideways


def load_cf203():
    lines = [l for l in open(TH) if not l.startswith("#")]
    hdr = lines[0].split()
    i_t = hdr.index("Time(s)")
    i_f = hdr.index("VAL_005_CF203_DownLeg")
    out = []
    for l in lines[1:]:
        parts = l.split()
        if parts:
            out.append((float(parts[i_t]), float(parts[i_f])))
    return out


def to_px(t, f):
    x = X0_PX + (t - T0) * (X1_PX - X0_PX) / (T1 - T0)
    y = Y0_PX + (f - F0) * (Y1_PX - Y0_PX) / (F1 - F0)
    return x, y


def polyline(points):
    return [to_px(t, f) for t, f in points]


def draw_dashed(draw, pts, fill, width, dash=9, gap=6):
    dist = 0.0
    on = True
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        seg = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if on:
            draw.line([(x1, y1), (x2, y2)], fill=fill, width=width)
        dist += seg
        limit = dash if on else gap
        if dist >= limit:
            dist = 0.0
            on = not on


def main():
    img = render_page()
    draw = ImageDraw.Draw(img)
    data = load_cf203()

    raw = [(t, f) for t, f in data if T0 <= t <= T1]
    draw.line(polyline(raw), fill=(200, 30, 30), width=3)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
    except OSError:
        font = ImageFont.load_default()
    lx, ly = X0_PX + 30, Y1_PX - 150
    draw.line([(lx, ly + 9), (lx + 46, ly + 9)], fill=(200, 30, 30), width=3)
    draw.text((lx + 56, ly - 4), "TRACE / trace_force CF203 (true scale)",
              fill=(200, 30, 30), font=font)

    img.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
