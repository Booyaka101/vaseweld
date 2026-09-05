"""Render the toolpath around a weld seam to a PNG, for the README.

Development tool, not part of the shipped package. Needs Pillow:

    python -m pip install pillow
    python tools/render_preview.py --gcode out.gcode --at 12.4 -o docs/weld-preview.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vaseweld.parser import parse_file, parse_move  # noqa: E402

BELOW = (70, 130, 190)
ABOVE = (225, 120, 60)
BACKGROUND = (250, 250, 249)
TEXT = (60, 60, 65)
SUPERSAMPLE = 3


def segments(gcode, low: float, high: float):
    """Extruding segments with Z in [low, high], as ((x0, y0, z0), (x1, y1, z1))."""
    x = y = z = None
    out = []
    for line in gcode.lines:
        move = parse_move(line)
        if move is None:
            continue
        nx = move.x if move.x is not None else x
        ny = move.y if move.y is not None else y
        nz = move.z if move.z is not None else z
        if (
            move.e is not None
            and move.e > 0
            and None not in (x, y, z, nx, ny, nz)
            and low <= nz <= high
        ):
            out.append(((x, y, z), (nx, ny, nz)))
        x, y, z = nx, ny, nz
    return out


def project(point, scale, tilt):
    """Side view with Z exaggerated, so 0.2 mm layers are legible next to a 20 mm part."""
    x, _, z = point
    return x * scale, -z * scale * tilt


def render(gcode, cut_z: float, span: float, size: tuple[int, int], tilt: float) -> Image.Image:
    width, height = (value * SUPERSAMPLE for value in size)
    picture = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(picture)

    lines = segments(gcode, cut_z - span, cut_z + span)
    if not lines:
        raise SystemExit(f"no extrusions found between Z {cut_z - span} and {cut_z + span}")
    centre = sum(point[1] for segment in lines for point in segment) / (2 * len(lines))
    lines = [s for s in lines if (s[0][1] + s[1][1]) / 2 <= centre]

    flat = [project(point, 1.0, tilt) for segment in lines for point in segment]
    min_u, max_u = min(u for u, _ in flat), max(u for u, _ in flat)
    min_v, max_v = min(v for _, v in flat), max(v for _, v in flat)
    margin = 0.06 * width
    span_u, span_v = max(max_u - min_u, 1e-6), max(max_v - min_v, 1e-6)
    scale = min((width - 2 * margin) / span_u, (height - 2 * margin) / span_v)
    offset_u = (width - span_u * scale) / 2
    offset_v = (height - span_v * scale) / 2 + 0.04 * height

    def place(point):
        u, v = project(point, scale, tilt)
        return (u - min_u * scale + offset_u, v - min_v * scale + offset_v)

    for start, end in sorted(lines, key=lambda s: -s[0][1]):
        colour = BELOW if end[2] < cut_z - 1e-6 else ABOVE
        draw.line([place(start), place(end)], fill=colour, width=SUPERSAMPLE)

    picture = picture.resize(size, Image.LANCZOS)
    draw = ImageDraw.Draw(picture)
    try:
        font = ImageFont.truetype("segoeui.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    draw.text((14, 12), f"above Z={cut_z:g}: spiral vase, one continuous helix", ABOVE, font=font)
    draw.text((14, 32), f"below Z={cut_z:g}: normal layers, walls and infill", BELOW, font=font)
    return picture


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gcode", type=Path, required=True)
    ap.add_argument("--at", type=float, required=True, help="the weld Z")
    ap.add_argument("--span", type=float, default=1.2, help="mm of print shown either side")
    ap.add_argument("--tilt", type=float, default=6.0, help="Z exaggeration")
    ap.add_argument("--width", type=int, default=900)
    ap.add_argument("--height", type=int, default=520)
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()

    picture = render(
        parse_file(args.gcode), args.at, args.span, (args.width, args.height), args.tilt
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    picture.save(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
