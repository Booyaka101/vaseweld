"""Work out where the plastic lands, and draw a cross-section through the wall.

Klipper proves the moves are executable. It says nothing about the bead they leave.
This walks every extruding move, converts the filament it consumes into a bead of
known width and height, and cuts a radial section through the result, which is what
you would see if you sawed the printed part in half.

The bead model is the usual one: a rectangle with semicircular ends, so an extrusion
of cross-section A at layer height h is w wide, where A = h*w - h^2 + pi*h^2/4.

Needs numpy and pillow:  python -m pip install numpy pillow
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vaseweld.parser import GcodeFile, parse_file, parse_move, walk  # noqa: E402
from vaseweld.weld import state_at  # noqa: E402

BACKGROUND = (250, 250, 249)
BELOW = (70, 130, 190)
ABOVE = (225, 120, 60)
GRID = (215, 215, 212)


@dataclass(slots=True)
class Bead:
    """One deposited segment, seen where it crosses the cutting half-plane."""

    radius: float  # distance from the part centre, mm
    z: float  # top of the bead, mm
    width: float  # mm
    height: float  # mm
    layer: int


def _config_float(gcode: GcodeFile, key: str, fallback: float) -> float:
    for piece in gcode.config.get(key, "").split(","):
        try:
            return float(piece.strip())
        except ValueError:
            continue
    return fallback


def bead_width(area: float, height: float) -> float:
    """Width of a rectangle-with-round-ends bead of cross-section ``area``."""
    if height <= 0:
        return 0.0
    return (area + height * height * (1.0 - math.pi / 4.0)) / height


def centre_of(gcode: GcodeFile) -> tuple[float, float]:
    """Midpoint of the first layer's extrusions, which is the part's axis."""
    xs, ys = [], []
    cursor = gcode.cursor()
    layer = gcode.layers[0]
    for step in walk(gcode.lines[: layer.end], cursor):
        if step.kind == "move" and step.delta and step.delta > 0 and step.dist > 0:
            if cursor.x is not None and cursor.y is not None:
                xs.append(cursor.x)
                ys.append(cursor.y)
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def deposit(gcode: GcodeFile, angle_deg: float) -> list[Bead]:
    """Every bead that crosses the vertical half-plane at ``angle_deg`` from the centre."""
    cx, cy = centre_of(gcode)
    theta = math.radians(angle_deg)
    ux, uy = math.cos(theta), math.sin(theta)
    diameter = _config_float(gcode, "filament_diameter", 1.75)
    filament_area = math.pi * (diameter / 2.0) ** 2
    height = _config_float(gcode, "layer_height", 0.2)

    bounds = [(layer.start, layer.index) for layer in gcode.layers]
    beads: list[Bead] = []
    cursor = gcode.cursor()
    previous = (None, None, None)
    layer_index = 0
    for step in walk(gcode.lines, cursor):
        while layer_index < len(bounds) and step.index >= bounds[layer_index][0]:
            layer_index += 1
        if step.kind != "move":
            continue
        move = parse_move(step.line)
        here = (cursor.x, cursor.y, move.z if move and move.z is not None else previous[2])
        if (
            step.delta
            and step.delta > 0
            and step.dist > 0
            and None not in previous
            and None not in here
        ):
            side_a = (previous[0] - cx) * uy - (previous[1] - cy) * ux
            side_b = (here[0] - cx) * uy - (here[1] - cy) * ux
            if (side_a > 0) != (side_b > 0) and side_a != side_b:
                fraction = side_a / (side_a - side_b)
                qx = previous[0] + fraction * (here[0] - previous[0])
                qy = previous[1] + fraction * (here[1] - previous[1])
                radius = (qx - cx) * ux + (qy - cy) * uy
                if radius > 0:
                    area = step.delta * filament_area / step.dist
                    beads.append(
                        Bead(
                            radius=radius,
                            z=previous[2] + fraction * (here[2] - previous[2]),
                            width=bead_width(area, height),
                            height=height,
                            layer=max(1, layer_index),
                        )
                    )
        previous = here
    return beads


@dataclass(slots=True)
class Trace:
    """One extruding move of a single layer, seen from above."""

    x0: float
    y0: float
    x1: float
    y1: float
    width: float


def plan_layer(gcode: GcodeFile, layer_index: int) -> list[Trace]:
    """Every extruding move of one layer, with the bead width it lays."""
    layer = gcode.layers[layer_index - 1]
    diameter = _config_float(gcode, "filament_diameter", 1.75)
    height = _config_float(gcode, "layer_height", 0.2)
    filament_area = math.pi * (diameter / 2.0) ** 2
    cursor = state_at(gcode, layer.start)
    traces: list[Trace] = []
    previous = (cursor.x, cursor.y)
    for step in walk(gcode.lines[layer.start : layer.end], cursor):
        if step.kind != "move":
            continue
        here = (cursor.x, cursor.y)
        if (
            step.delta
            and step.delta > 0
            and step.dist > 0
            and None not in previous
            and None not in here
        ):
            traces.append(
                Trace(
                    x0=previous[0],
                    y0=previous[1],
                    x1=here[0],
                    y1=here[1],
                    width=bead_width(step.delta * filament_area / step.dist, height),
                )
            )
        previous = here
    return traces


def render_plan(
    traces: list[Trace], *, pixels_per_mm: float = 34.0, margin: float = 1.0, label: str = ""
) -> Image.Image:
    """Draw one layer from above, every bead at its true width."""
    xs = [v for t in traces for v in (t.x0, t.x1)]
    ys = [v for t in traces for v in (t.y0, t.y1)]
    x_low, x_high = min(xs) - margin, max(xs) + margin
    y_low, y_high = min(ys) - margin, max(ys) + margin
    width = int((x_high - x_low) * pixels_per_mm)
    height = int((y_high - y_low) * pixels_per_mm)
    canvas = np.tile(np.array(BACKGROUND, dtype=np.float32) / 255.0, (height, width, 1))
    yy, xx = np.mgrid[0:height, 0:width]

    for trace in traces:
        ax = (trace.x0 - x_low) * pixels_per_mm
        ay = height - (trace.y0 - y_low) * pixels_per_mm
        bx = (trace.x1 - x_low) * pixels_per_mm
        by = height - (trace.y1 - y_low) * pixels_per_mm
        radius = max(trace.width / 2 * pixels_per_mm, 0.6)
        lo_x = max(int(min(ax, bx) - radius) - 2, 0)
        hi_x = min(int(max(ax, bx) + radius) + 3, width)
        lo_y = max(int(min(ay, by) - radius) - 2, 0)
        hi_y = min(int(max(ay, by) + radius) + 3, height)
        if lo_x >= hi_x or lo_y >= hi_y:
            continue
        px = xx[lo_y:hi_y, lo_x:hi_x] - ax
        py = yy[lo_y:hi_y, lo_x:hi_x] - ay
        vx, vy = bx - ax, by - ay
        length2 = vx * vx + vy * vy
        t = np.clip((px * vx + py * vy) / length2, 0.0, 1.0) if length2 > 0 else 0.0
        dist = np.hypot(px - t * vx, py - t * vy)
        # A bead much thinner than the nozzle can lay is a dragged thread, not a wall.
        tint = ABOVE if trace.width > 0.2 else (200, 40, 40)
        canvas[lo_y:hi_y, lo_x:hi_x] = np.where(
            (dist <= radius)[..., None],
            np.array(tint, dtype=np.float32) / 255.0,
            canvas[lo_y:hi_y, lo_x:hi_x],
        )

    picture = Image.fromarray((np.clip(canvas, 0, 1) * 255).astype(np.uint8))
    return _caption_plan(picture, label) if label else picture


def _caption_plan(picture: Image.Image, label: str) -> Image.Image:
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("segoeui.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    out = Image.new("RGB", (picture.width, picture.height + 30), BACKGROUND)
    out.paste(picture, (0, 30))
    ImageDraw.Draw(out).text((10, 7), label, (55, 55, 60), font=font)
    return out


def render(
    beads: list[Bead],
    z_low: float,
    z_high: float,
    cut_z: float,
    cut_layer: int,
    *,
    wall_span: float = 1.7,
    pixels_per_mm: float = 190.0,
    label: str = "",
) -> Image.Image:
    """Draw the wall in section: radius across, height up, beads to scale."""
    window = [b for b in beads if z_low - b.height <= b.z <= z_high]
    if not window:
        raise SystemExit(f"no beads between Z {z_low} and {z_high}")
    outer = max(b.radius for b in window)
    r_low, r_high = outer - wall_span + 0.5, outer + 0.5

    width = int((r_high - r_low) * pixels_per_mm)
    height = int((z_high - z_low) * pixels_per_mm)
    canvas = np.tile(np.array(BACKGROUND, dtype=np.float32) / 255.0, (height, width, 1))
    yy, xx = np.mgrid[0:height, 0:width]

    for bead in sorted(window, key=lambda b: (b.layer, b.z)):
        px = (bead.radius - r_low) * pixels_per_mm
        py = height - (bead.z - bead.height / 2 - z_low) * pixels_per_mm
        half_w = bead.width / 2 * pixels_per_mm
        half_h = bead.height / 2 * pixels_per_mm
        if half_w <= 0 or half_h <= 0:
            continue
        x0 = max(int(px - half_w) - 2, 0)
        x1 = min(int(px + half_w) + 3, width)
        y0 = max(int(py - half_h) - 2, 0)
        y1 = min(int(py + half_h) + 3, height)
        if x0 >= x1 or y0 >= y1:
            continue
        dx = np.abs(xx[y0:y1, x0:x1] - px)
        dy = np.abs(yy[y0:y1, x0:x1] - py)
        # a rectangle of length (w - h) capped with semicircles of radius h/2
        flat = max(half_w - half_h, 0.0)
        inside = (dy <= half_h) & ((dx <= flat) | (((dx - flat) ** 2 + dy**2) <= half_h**2))
        # Colour by which file the layer came from, not by height: a spiral layer
        # ramps its Z, so the weld layer itself sits below the nominal weld height.
        tint = np.array(ABOVE if bead.layer >= cut_layer else BELOW, dtype=np.float32) / 255.0
        edge = tint * 0.72
        rim = inside & (dy > half_h - 1.6)
        patch = canvas[y0:y1, x0:x1]
        patch[inside] = tint
        patch[rim] = edge

    # a hairline at the weld height
    seam_row = height - int((cut_z - z_low) * pixels_per_mm)
    if 0 <= seam_row < height:
        canvas[seam_row : seam_row + 1, :] = np.array(GRID, dtype=np.float32) / 255.0

    picture = Image.fromarray((np.clip(canvas, 0, 1) * 255).astype(np.uint8))
    if label:
        picture = _caption(picture, label, cut_z)
    return picture


def _caption(picture: Image.Image, label: str, cut_z: float) -> Image.Image:
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("segoeui.ttf", 15)
        small = ImageFont.truetype("segoeui.ttf", 13)
    except OSError:
        font = small = ImageFont.load_default()
    out = Image.new("RGB", (picture.width, picture.height + 46), BACKGROUND)
    out.paste(picture, (0, 46))
    draw = ImageDraw.Draw(out)
    draw.text((10, 8), label, (55, 55, 60), font=font)
    draw.text(
        (10, 27), f"section through the wall at the Z={cut_z:g} weld", (130, 130, 135), font=small
    )
    return out


def side_by_side(panels: list[Image.Image], gap: int = 26) -> Image.Image:
    width = sum(p.width for p in panels) + gap * (len(panels) - 1)
    height = max(p.height for p in panels)
    out = Image.new("RGB", (width, height), BACKGROUND)
    x = 0
    for panel in panels:
        out.paste(panel, (x, 0))
        x += panel.width + gap
    return out


def layer_report(beads: list[Bead], cut_layer: int, span: int = 4) -> list[tuple[int, float, int]]:
    by_layer: dict[int, list[float]] = {}
    for bead in beads:
        by_layer.setdefault(bead.layer, []).append(bead.width)
    rows = []
    for layer in range(cut_layer - span, cut_layer + span + 1):
        widths = by_layer.get(layer)
        if widths:
            rows.append((layer, statistics.median(widths), len(widths)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("gcode", type=Path)
    ap.add_argument("--at", type=float, required=True, help="the weld Z")
    ap.add_argument("--cut-layer", type=int, required=True, help="layer number of the weld")
    ap.add_argument("--angle", type=float, default=30.0, help="where to saw the part, degrees")
    ap.add_argument("--span", type=float, default=1.0, help="mm shown above and below the weld")
    ap.add_argument("--plan", action="store_true", help="draw the weld layer from above")
    ap.add_argument("--compare", type=Path, help="a second file to draw beside the first")
    ap.add_argument("--labels", default="", help="comma separated panel captions")
    ap.add_argument("-o", "--out", type=Path)
    args = ap.parse_args()

    gcode = parse_file(args.gcode)
    beads = deposit(gcode, args.angle)
    print(f"{args.gcode.name}: {len(beads)} beads cross the cut plane")

    if args.cut_layer:
        print(f"\n  {'layer':>6s} {'median bead width':>19s} {'beads':>7s}")
        for layer, width, count in layer_report(beads, args.cut_layer):
            mark = "  <- weld" if layer == args.cut_layer else ""
            print(f"  {layer:6d} {width:16.3f} mm {count:7d}{mark}")

    if args.out:
        labels = [part.strip() for part in args.labels.split(",")] if args.labels else []
        sources = [(gcode, beads)]
        if args.compare:
            other = parse_file(args.compare)
            sources.append((other, deposit(other, args.angle)))
        if args.plan:
            panels = [
                render_plan(
                    plan_layer(source, args.cut_layer),
                    label=labels[i] if i < len(labels) else source.path.name,
                )
                for i, (source, _) in enumerate(sources)
            ]
            picture = side_by_side(panels) if len(panels) > 1 else panels[0]
            args.out.parent.mkdir(parents=True, exist_ok=True)
            picture.save(args.out)
            print(f"\nwrote {args.out}")
            return
        panels = [
            render(
                found,
                args.at - args.span,
                args.at + args.span,
                args.at,
                args.cut_layer,
                label=labels[i] if i < len(labels) else source.path.name,
            )
            for i, (source, found) in enumerate(sources)
        ]
        picture = side_by_side(panels) if len(panels) > 1 else panels[0]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        picture.save(args.out)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
