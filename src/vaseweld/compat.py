"""Refuse to weld two files that were not sliced from the same plate.

Everything here is checked before a single line is written, so a mismatch costs
the user a message rather than a failed print.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parser import GcodeFile, find_objects_info, parse_move, strip_comment

# Quoted verbatim from PrusaSlicer's own validator (Print::validate).
SINGLE_MATERIAL_MSG = (
    "The Spiral Vase option can only be used when printing single material objects."
)
SINGLE_OBJECT_MSG = (
    "Only a single object may be printed at a time in Spiral Vase mode. "
    'Either remove all but the last object, or enable sequential mode by "complete_objects".'
)

COMPARED_FIELDS = (
    "layer_height",
    "first_layer_height",
    "nozzle_diameter",
    "filament_diameter",
    "bed_shape",
    "printer_model",
)

_TOOL = re.compile(r"^\s*T(\d+)\s*$")
_SKIRT_TYPE = re.compile(r"^\s*;\s*TYPE:\s*(.*?)\s*$", re.IGNORECASE)
_POLYGON = re.compile(r'"polygon"\s*:\s*\[(.*?)\]\]', re.DOTALL)
_PLACEMENT_TOLERANCE_MM = 0.05


class CompatError(Exception):
    """The two files cannot be welded. The message names the offending field."""


@dataclass(frozen=True)
class Footprint:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def differs_from(self, other: "Footprint", tolerance: float) -> bool:
        return any(
            abs(a - b) > tolerance
            for a, b in zip(
                (self.min_x, self.min_y, self.max_x, self.max_y),
                (other.min_x, other.min_y, other.max_x, other.max_y),
            )
        )

    def __str__(self) -> str:
        return f"X {self.min_x:.3f}-{self.max_x:.3f}, Y {self.min_y:.3f}-{self.max_y:.3f}"


def normalise(field: str, value: str) -> str:
    """Collapse the formatting differences between slicers (``0.200`` vs ``0.2``)."""
    value = value.strip()
    if field == "bed_shape":
        parts = []
        for point in value.split(","):
            coords = point.strip().split("x")
            try:
                parts.append("x".join(f"{float(c):g}" for c in coords))
            except ValueError:
                parts.append(point.strip())
        return ",".join(parts)
    pieces = []
    for piece in value.split(","):
        piece = piece.strip()
        try:
            pieces.append(f"{float(piece):g}")
        except ValueError:
            pieces.append(piece)
    return ",".join(pieces)


def tools_used(gcode: GcodeFile) -> set[int]:
    """Tool indices actually selected in the print body."""
    tools: set[int] = set()
    for line in gcode.lines[gcode.header_end : gcode.footer_start]:
        code, _ = strip_comment(line)
        match = _TOOL.match(code)
        if match:
            tools.add(int(match.group(1)))
    return tools or {0}


def first_layer_footprint(gcode: GcodeFile) -> Footprint | None:
    """Bounding box of the object extrusions on layer 1, skirt and brim excluded."""
    layer = gcode.layers[0]
    x = y = None
    section = ""
    xs: list[float] = []
    ys: list[float] = []
    for line in gcode.layer_lines(layer):
        marker = _SKIRT_TYPE.match(line)
        if marker:
            section = marker.group(1).lower()
        move = parse_move(line)
        if move is None:
            continue
        if move.x is not None:
            x = move.x
        if move.y is not None:
            y = move.y
        if move.e is None or move.e <= 0:
            continue
        if move.x is None and move.y is None:
            continue
        if "skirt" in section or "brim" in section:
            continue
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return Footprint(min(xs), min(ys), max(xs), max(ys))


def object_polygons(gcode: GcodeFile) -> list[str] | None:
    """Rounded object outlines from ``objects_info``, if the slicer wrote them."""
    info = find_objects_info(gcode.lines, gcode.config)
    if not info:
        return None
    polygons = []
    for body in _POLYGON.findall(info):
        points = re.findall(r"-?\d*\.?\d+", body)
        polygons.append(",".join(f"{float(p):.2f}" for p in points))
    return sorted(polygons) or None


def _label(gcode: GcodeFile) -> str:
    return gcode.path.name


def check_compatible(a: GcodeFile, b: GcodeFile) -> None:
    """Raise CompatError unless the two files describe the same single-material plate."""
    for gcode in (a, b):
        tools = tools_used(gcode)
        if len(tools) > 1:
            raise CompatError(
                f"{_label(gcode)} uses {len(tools)} extruders "
                f"(tools {', '.join('T' + str(t) for t in sorted(tools))}). "
                f"{SINGLE_MATERIAL_MSG}"
            )
        if gcode.object_instances is not None and gcode.object_instances > 1:
            raise CompatError(
                f"{_label(gcode)} contains {gcode.object_instances} object instances. "
                f"{SINGLE_OBJECT_MSG}"
            )

    for key in COMPARED_FIELDS:
        left, right = a.config.get(key), b.config.get(key)
        if left is None and right is None:
            continue
        if left is None or right is None:
            present, missing = (a, b) if right is None else (b, a)
            raise CompatError(
                f"config mismatch on '{key}': {_label(present)} sets it, "
                f"{_label(missing)} does not. Re-slice both files from the same project."
            )
        if normalise(key, left) != normalise(key, right):
            raise CompatError(
                f"config mismatch on '{key}': "
                f"{_label(a)} = {left!r}, {_label(b)} = {right!r}. "
                "Both files must come from the same plate and printer profile."
            )

    count_a, count_b = a.object_instances, b.object_instances
    if count_a is not None and count_b is not None and count_a != count_b:
        raise CompatError(
            f"config mismatch on 'object instance count': "
            f"{_label(a)} has {count_a}, {_label(b)} has {count_b}."
        )

    polygons_a, polygons_b = object_polygons(a), object_polygons(b)
    if polygons_a and polygons_b and polygons_a != polygons_b:
        raise CompatError(
            f"config mismatch on 'object placement': the object outlines in "
            f"{_label(a)} and {_label(b)} differ. Do not move or rescale the object "
            "between the two slices."
        )

    footprint_a, footprint_b = first_layer_footprint(a), first_layer_footprint(b)
    if footprint_a is None or footprint_b is None:
        return
    if footprint_a.differs_from(footprint_b, _PLACEMENT_TOLERANCE_MM):
        raise CompatError(
            f"config mismatch on 'object placement': first layer covers {footprint_a} "
            f"in {_label(a)} but {footprint_b} in {_label(b)}. "
            "Do not move or rescale the object between the two slices."
        )
