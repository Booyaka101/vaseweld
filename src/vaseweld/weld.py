"""Splice two sliced files at a layer boundary and ramp the flow across the seam.

The whole output is emitted with relative extrusion (M83). OrcaSlicer's
SpiralVase.cpp carries the note that "Tapering of the transition layer only
works reliably with relative extruder distances", and the same applies here:
a scaled E value is only meaningful as a delta.
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field

from . import __version__
from .parser import (
    Cursor,
    GcodeFile,
    Layer,
    format_e,
    parse_e_mode,
    parse_move,
    set_e,
    walk,
)

DEFAULT_STARTING_FLOW_RATIO = 0.8
DEFAULT_FINISHING_FLOW_RATIO = 0.25

_M73 = re.compile(r"^\s*M73\b", re.IGNORECASE)
_M73_P = re.compile(r"\bP(\d+)")
_M73_R = re.compile(r"\bR(\d+)")
_STATS_INFO = re.compile(r"^\s*SET_PRINT_STATS_INFO\b", re.IGNORECASE)
_TOTAL_LAYER = re.compile(r"(TOTAL_LAYER\s*=\s*)(\d+)", re.IGNORECASE)
_CURRENT_LAYER = re.compile(r"(CURRENT_LAYER\s*=\s*)(\d+)", re.IGNORECASE)
_TOTAL_LAYER_COMMENT = re.compile(
    r"^(\s*;\s*total layers? (?:count|number)\s*[:=]\s*)(\d+)", re.IGNORECASE
)
_TIME_COMMENT = re.compile(r"^\s*;\s*estimated .*printing time", re.IGNORECASE)
_FILAMENT_COMMENT = re.compile(
    r"^\s*;\s*(filament used \[(?:mm|cm3|g)\]|total filament used \[g\]"
    r"|total filament cost|total filament used for wipe tower \[g\])\s*=",
    re.IGNORECASE,
)
# BambuStudio writes its totals in a header block with colons instead of equals,
# and labels the volume cm^3 while computing it in mm^3. Both are reproduced as-is
# so the file stays consistent with what that slicer would have written.
_BAMBU_TOTAL = re.compile(
    r"^(\s*;\s*total filament (?:length \[mm\]|volume \[cm\^3\]|weight \[g\])\s*:\s*).*$",
    re.IGNORECASE,
)
_BAMBU_TIME = re.compile(r"^\s*;\s*model printing time\s*:", re.IGNORECASE)
_DURATION = re.compile(
    r"\s*(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?\s*", re.IGNORECASE
)


class WeldError(Exception):
    """The requested weld cannot be performed."""


@dataclass
class WeldResult:
    lines: list[str]
    newline: str
    cut_z: float
    requested_z: float
    cut_layer: int
    bottom_role: str
    top_role: str
    bottom_range: tuple[int, int]
    top_range: tuple[int, int]
    bottom_z: tuple[float, float]
    top_z: tuple[float, float]
    e_mode_note: str
    ramp_note: str
    stats_note: str
    stats_removed: list[str]
    warnings: list[str]

    def summary(self) -> list[str]:
        out = []
        if abs(self.requested_z - self.cut_z) > 1e-9:
            out.append(f"requested Z={self.requested_z:.3f} is between layers, snapping down")
        out.append(f"cut snapped to Z={self.cut_z:.3f} (layer {self.cut_layer})")
        out.append(
            f"{self.bottom_role}: layers {self.bottom_range[0]}-{self.bottom_range[1]} "
            f"(Z {self.bottom_z[0]:.3f}-{self.bottom_z[1]:.3f})"
        )
        out.append(
            f"{self.top_role}: layers {self.top_range[0]}-{self.top_range[1]} "
            f"(Z {self.top_z[0]:.3f}-{self.top_z[1]:.3f})"
        )
        out.append(f"E mode: {self.e_mode_note}")
        out.append(f"transition ramp: {self.ramp_note}")
        return out


def _ramp_factors(
    lines: list[str], cursor: Cursor, *, ramp_in: bool, flow: float
) -> dict[int, float]:
    """Per-line E scaling for a transition layer, mirroring OrcaSlicer's SpiralVase."""
    lengths = [
        (step.index, step.dist)
        for step in walk(lines, cursor.copy())
        if step.kind == "move" and step.delta is not None and step.delta > 0 and step.dist > 0
    ]
    total = sum(length for _, length in lengths)
    if total <= 0:
        return {}
    factors: dict[int, float] = {}
    travelled = 0.0
    for index, length in lengths:
        travelled += length
        fraction = travelled / total
        factors[index] = (
            flow + fraction * (1.0 - flow) if ramp_in else flow + (1.0 - fraction) * (1.0 - flow)
        )
    return factors


class _Sink:
    """Collects welded output, rewriting E into relative form as it goes."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.net_e = 0.0
        self.saw_mode_command = False

    def append_generated(self, lines: list[str], cursor: Cursor | None = None) -> None:
        """Append lines vaseweld wrote itself, which are already relative E.

        Their E is counted towards the filament total, and any XY they carry is
        pushed into the cursor so later distances stay right.
        """
        for line in lines:
            self.lines.append(line)
            move = parse_move(line)
            if move is None:
                continue
            self.net_e += move.e or 0.0
            if cursor is not None:
                cursor.x = move.x if move.x is not None else cursor.x
                cursor.y = move.y if move.y is not None else cursor.y

    def feed(
        self, lines: list[str], cursor: Cursor, factors: dict[int, float] | None = None
    ) -> None:
        factors = factors or {}
        for step in walk(lines, cursor):
            if step.kind == "mode":
                self.saw_mode_command = True
                if parse_e_mode(step.line):
                    self.lines.append(step.line)
                else:
                    self.lines.append("M83 ; vaseweld: was M82, output is relative E")
                continue
            if step.kind == "move" and step.delta is not None:
                delta = step.delta
                factor = factors.get(step.index)
                if factor is not None and delta > 0:
                    delta = round(delta * factor, 5)
                self.net_e += delta
                self.lines.append(set_e(step.line, delta))
                continue
            self.lines.append(step.line)


def _config_floats(config: dict[str, str], key: str) -> list[float]:
    values = []
    for piece in config.get(key, "").split(","):
        try:
            values.append(float(piece.strip().rstrip("%")))
        except ValueError:
            continue
    return values


def _first_float(config: dict[str, str], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        values = _config_floats(config, key)
        if values:
            return values[0]
    return default


def flow_ratio(config: dict[str, str], key: str, fallback: float) -> float:
    """Read a spiral flow ratio from the slicer config, falling back when absent."""
    values = _config_floats(config, key)
    return values[0] if values else fallback


@dataclass(slots=True)
class _Opening:
    """How a layer starts, which decides what the seam needs before it."""

    printing_at: int | None = None  # first move that both extrudes and moves in XY
    retracts: bool = False
    positions: bool = False  # reaches its start point with a travel move


def _opening(lines: list[str], cursor: Cursor) -> _Opening:
    opening = _Opening()
    for step in walk(lines, cursor.copy()):
        if step.kind != "move":
            continue
        move = parse_move(step.line)
        moves_xy = move is not None and (move.x is not None or move.y is not None)
        if step.delta is not None and step.delta > 0 and moves_xy:
            opening.printing_at = step.index
            return opening
        if step.delta is not None and step.delta < 0:
            opening.retracts = True
        elif moves_xy:
            opening.positions = True
    return opening


@dataclass(slots=True)
class _Seam:
    """Lines the weld inserts, and where each group has to go."""

    at_boundary: list[str]  # straight after the boundary, before the layer's own moves
    before_printing: list[str]  # just before the layer's first printing move


def _seam_lines(
    config: dict[str, str],
    seam: Cursor,
    resume: Cursor,
    opening: _Opening,
    *,
    retract: bool,
) -> _Seam:
    """Bridge the gap between where the file below stopped and what the file above assumes.

    ``seam`` is the state the file below leaves, ``resume`` the state the file above
    takes for granted. Slicers disagree about who owns the layer-change retraction:
    PrusaSlicer and OrcaSlicer put it at the start of the next layer, BambuStudio at
    the end of the previous one. Welding them naively leaves the nozzle either
    double-retracted or double-primed, so the difference has to be made up here.
    """
    length = _first_float(config, "retract_length", "retraction_length")
    speed = _first_float(config, "retract_speed", "retraction_speed", default=35.0) or 35.0
    recover = _first_float(config, "deretract_speed", "retract_speed", "retraction_speed") or speed

    def move_e(amount: float, label: str) -> str:
        rate = recover if amount > 0 else speed
        return f"G1 E{format_e(amount)} F{int(round(rate * 60))} ; vaseweld: seam {label}"

    travels = opening.printing_at is not None and not opening.positions
    if not travels or seam.x is None or seam.y is None:
        # The layer gets itself into position and primes itself, so only the leftover
        # retraction difference needs correcting, and it has to happen first.
        difference = seam.retracted - resume.retracted
        if abs(difference) < 1e-4:
            return _Seam([], [])
        return _Seam([move_e(difference, "prime" if difference > 0 else "retract")], [])

    lines = []
    top_up = max(0.0, length - seam.retracted) if retract else 0.0
    if top_up > 1e-4:
        lines.append(move_e(-top_up, "retract"))
    travel_speed = _first_float(config, "travel_speed", default=130.0) or 130.0
    lines.append(
        f"G1 X{seam.x:g} Y{seam.y:g} F{int(round(travel_speed * 60))}"
        " ; vaseweld: seam travel to where the next slice expects the nozzle"
    )
    prime = seam.retracted + top_up - resume.retracted
    if prime > 1e-4:
        lines.append(move_e(prime, "unretract"))
    return _Seam([], lines)


def state_at(gcode: GcodeFile, line_index: int) -> Cursor:
    """Extruder and nozzle state the file has reached by ``line_index``.

    The layers taken from a file assume the nozzle is where that file left it, so
    the weld has to resume from this state rather than from nothing.
    """
    cursor = Cursor(relative_e=gcode.relative_e)
    for _ in walk(gcode.lines[:line_index], cursor):
        pass
    return cursor


def _snap(top: GcodeFile, cut_z: float) -> Layer:
    lowest, highest = top.layers[1].z, top.layers[-1].z
    if cut_z < lowest - 1e-9 or cut_z > highest + 1e-9:
        raise WeldError(
            f"cut Z={cut_z:.3f} is outside the weldable range. "
            f"Valid range is Z {lowest:.3f} to {highest:.3f} "
            f"(layers 2 to {len(top.layers)} of {top.path.name})."
        )
    return [layer for layer in top.layers if layer.z <= cut_z + 1e-9][-1]


def _parse_duration(text: str) -> float | None:
    match = _DURATION.fullmatch(text)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    days, total = divmod(total, 86400)
    hours, total = divmod(total, 3600)
    minutes, secs = divmod(total, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    if days or hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _time_comment_seconds(lines: list[str], needle: str) -> float | None:
    for line in lines:
        if needle in line.lower() and "=" in line:
            return _parse_duration(line.split("=", 1)[1])
    return None


def _m73_remaining(lines: list[str]) -> float | None:
    for line in lines:
        if _M73.match(line):
            match = _M73_R.search(line)
            if match:
                return float(match.group(1))
    return None


@dataclass
class _Progress:
    """Remaps M73 reporting across the seam using the two files' own R values.

    R is minutes remaining, so elapsed-at-the-cut is ``total - R`` in the bottom
    file and the top file's own R values are already correct after the cut.
    """

    bottom_total: float
    bottom_remaining_at_cut: float
    top_remaining_at_cut: float

    @property
    def welded_total(self) -> float:
        return (self.bottom_total - self.bottom_remaining_at_cut) + self.top_remaining_at_cut

    def rewrite(self, line: str, *, from_bottom: bool) -> str:
        found = _M73_R.search(line)
        if found is None:
            return line
        total = self.welded_total
        value = float(found.group(1))
        remaining = max(0.0, total - self.bottom_total + value) if from_bottom else value
        elapsed = max(0.0, total - remaining)
        percent = 0 if total <= 0 else min(100, int(round(100 * elapsed / total)))
        line = _M73_R.sub(f"R{int(round(remaining))}", line, count=1)
        return _M73_P.sub(f"P{percent}", line, count=1)


def _build_progress(
    bottom: GcodeFile, bottom_end: int, top: GcodeFile, top_start: int
) -> _Progress | None:
    total = _m73_remaining(bottom.lines)
    at_cut = _m73_remaining(list(reversed(bottom.lines[:bottom_end])))
    top_at_cut = _m73_remaining(top.lines[top_start:])
    if total is None or at_cut is None or top_at_cut is None:
        return None
    return _Progress(total, at_cut, top_at_cut)


def _material_totals(net_e_mm: float, config: dict[str, str]) -> dict[str, float]:
    diameter = _first_float(config, "filament_diameter", default=1.75) or 1.75
    volume_cm3 = net_e_mm * math.pi * (diameter / 2.0) ** 2 / 1000.0
    grams = volume_cm3 * _first_float(config, "filament_density")
    return {
        "filament used [mm]": net_e_mm,
        "filament used [cm3]": volume_cm3,
        "total filament used [g]": grams,
        "total filament cost": grams / 1000.0 * _first_float(config, "filament_cost"),
    }


def _renumber_layers(
    body: list[str], footer: list[str], spans: list[tuple[int, int]], total_layers: int
) -> None:
    """Fix Klipper's SET_PRINT_STATS_INFO counters and the total-layer comments.

    A marker is numbered by the output layer it sits in, so a file whose first
    layer carries no marker keeps its off-by-one relationship intact.
    """
    starts = [start for start, _ in spans]
    for offset, chunk in ((0, body), (len(body), footer)):
        for i, line in enumerate(chunk):
            if _STATS_INFO.match(line):
                line = _TOTAL_LAYER.sub(lambda m: f"{m.group(1)}{total_layers}", line)
                if _CURRENT_LAYER.search(line):
                    position = bisect.bisect_right(starts, i + offset)
                    number = spans[position - 1][1] if position else 1
                    line = _CURRENT_LAYER.sub(lambda m: f"{m.group(1)}{number}", line)
                chunk[i] = line
            elif _TOTAL_LAYER_COMMENT.match(line):
                chunk[i] = _TOTAL_LAYER_COMMENT.sub(lambda m: f"{m.group(1)}{total_layers}", line)


def _welded_seconds(bottom: GcodeFile, top: GcodeFile, progress: _Progress | None) -> float | None:
    """Split each file's own time estimate at the cut using its M73 remaining times."""
    if progress is None or progress.bottom_total <= 0:
        return None
    bottom_seconds = _time_comment_seconds(bottom.lines, "estimated printing time")
    top_seconds = _time_comment_seconds(top.lines, "estimated printing time")
    top_total = _m73_remaining(top.lines)
    if bottom_seconds is None or top_seconds is None or not top_total:
        return progress.welded_total * 60.0
    done = 1.0 - progress.bottom_remaining_at_cut / progress.bottom_total
    left = progress.top_remaining_at_cut / top_total
    return bottom_seconds * done + top_seconds * left


def _bambu_total(line: str, material: dict[str, float]) -> str:
    label = line.split(":", 1)[0]
    if "length" in label.lower():
        return f"{label}: {material['filament used [mm]']:.2f}"
    if "volume" in label.lower():
        return f"{label}: {material['filament used [cm3]'] * 1000:.2f}"
    return f"{label}: {material['total filament used [g]']:.2f}"


def _rewrite_stats(
    lines: list[str],
    *,
    seconds: float | None,
    first_layer_seconds: float | None,
    material: dict[str, float],
) -> None:
    out: list[str] = []
    for line in lines:
        if _BAMBU_TOTAL.match(line):
            out.append(_bambu_total(line, material))
            continue
        if _BAMBU_TIME.match(line):
            if seconds is not None:
                out.append(
                    f"; model printing time: {_format_duration(seconds)}; "
                    f"total estimated time: {_format_duration(seconds)}"
                )
            continue
        if _FILAMENT_COMMENT.match(line):
            key = line.split("=", 1)[0].strip(" ;").lower()
            if key in material:
                out.append(f"; {key} = {material[key]:.2f}")
            else:
                out.append(line)
            continue
        if _TIME_COMMENT.match(line):
            value = first_layer_seconds if "first layer" in line.lower() else seconds
            if value is not None:
                label = line.split("=", 1)[0].rstrip()
                out.append(f"{label} = {_format_duration(value)}")
            continue
        if re.match(r"^\s*;\s*use_relative_e_distances\s*=", line):
            out.append("; use_relative_e_distances = 1")
            continue
        out.append(line)
    lines[:] = out


def _provenance(
    *,
    bottom: GcodeFile,
    top: GcodeFile,
    bottom_role: str,
    top_role: str,
    cut_layer: Layer,
    kept_bottom: list[Layer],
    top_kept: list[Layer],
) -> list[str]:
    return [
        f"; vaseweld {__version__}: welded at Z={cut_layer.z:.3f} (layer {cut_layer.index})",
        f";   layers {kept_bottom[0].index}-{kept_bottom[-1].index} from "
        f"{bottom.path.name} ({bottom_role})",
        f";   layers {top_kept[0].index}-{top_kept[-1].index} from {top.path.name} ({top_role})",
        ";   extrusion is relative (M83) across the whole file",
    ]


def _e_mode_note(bottom: GcodeFile, top: GcodeFile) -> str:
    if bottom.relative_e and top.relative_e:
        return "relative (unchanged)"
    if not bottom.relative_e and not top.relative_e:
        return "absolute -> relative (converted)"
    return (
        f"mixed ({bottom.path.name}={'relative' if bottom.relative_e else 'absolute'}, "
        f"{top.path.name}={'relative' if top.relative_e else 'absolute'}) "
        "-> relative (converted)"
    )


@dataclass
class _Plan:
    """Which layers come from which file, and how the flow ramp is configured."""

    cut_layer: Layer
    kept_bottom: list[Layer]
    top_kept: list[Layer]
    start_flow: float
    finish_flow: float
    warnings: list[str] = field(default_factory=list)

    @property
    def bottom_last(self) -> Layer:
        return self.kept_bottom[-1]

    @property
    def total_layers(self) -> int:
        return len(self.kept_bottom) + len(self.top_kept)


@dataclass
class _Spliced:
    """The emitted file, still in two halves so the footer can be rewritten alone."""

    body: list[str]
    footer: list[str]
    spans: list[tuple[int, int]]  # (index in body, output layer number)
    seam_start: int
    net_e: float


def _plan(
    bottom: GcodeFile,
    top: GcodeFile,
    cut_z: float,
    *,
    bottom_role: str,
    top_role: str,
    start_flow: float | None,
    finish_flow: float | None,
) -> _Plan:
    cut_layer = _snap(top, cut_z)
    if cut_layer.index < 2:
        raise WeldError(
            f"cut Z={cut_z:.3f} lands on layer 1; at least one layer must come from "
            f"{bottom.path.name}. Valid range is Z {top.layers[1].z:.3f} to "
            f"{top.layers[-1].z:.3f}."
        )
    kept_bottom = [layer for layer in bottom.layers if layer.z < cut_layer.z - 1e-9]
    if not kept_bottom:
        raise WeldError(
            f"{bottom.path.name} has no layer below Z={cut_layer.z:.3f}; nothing to weld."
        )
    vase_config = top.config if top_role == "vase" else bottom.config
    return _Plan(
        cut_layer=cut_layer,
        kept_bottom=kept_bottom,
        top_kept=top.layers[cut_layer.index - 1 :],
        start_flow=start_flow
        if start_flow is not None
        else flow_ratio(vase_config, "spiral_starting_flow_ratio", DEFAULT_STARTING_FLOW_RATIO),
        finish_flow=finish_flow
        if finish_flow is not None
        else flow_ratio(vase_config, "spiral_finishing_flow_ratio", DEFAULT_FINISHING_FLOW_RATIO),
        warnings=_seam_step_warning(top, cut_layer, kept_bottom[-1]),
    )


def _seam_step_warning(top: GcodeFile, cut_layer: Layer, bottom_last: Layer) -> list[str]:
    """The layer above the cut was sliced expecting a particular layer below it.

    Only reachable through --force, because compat.py refuses a layer_height mismatch,
    but a forced weld can otherwise squeeze a thick layer into a thin gap in silence.
    """
    step = cut_layer.z - bottom_last.z
    expected = _first_float(top.config, "layer_height")
    if expected <= 0 or abs(step - expected) <= 0.02:
        return []
    return [
        f"the seam leaves a {step:.3f} mm step but {top.path.name} slices "
        f"{expected:.3f} mm layers, so layer {cut_layer.index} will lay "
        f"{expected / step:.0%} of the material that gap can take"
    ]


def _splice(
    bottom: GcodeFile,
    top: GcodeFile,
    plan: _Plan,
    *,
    bottom_role: str,
    top_role: str,
    seam_retract: bool,
) -> _Spliced:
    sink = _Sink()
    spans: list[tuple[int, int]] = []
    cursor = Cursor(relative_e=bottom.relative_e)
    sink.feed(bottom.lines[: bottom.header_end], cursor)
    if not sink.saw_mode_command:
        sink.lines += ["M83 ; vaseweld: output uses relative E", "G92 E0"]

    for layer in plan.kept_bottom:
        lines = bottom.lines[layer.start : layer.end]
        factors = (
            _ramp_factors(lines, cursor, ramp_in=False, flow=plan.finish_flow)
            if bottom_role == "vase" and layer is plan.bottom_last
            else None
        )
        spans.append((len(sink.lines), len(spans) + 1))
        sink.feed(lines, cursor, factors)

    seam_start = len(sink.lines)
    sink.lines.append(
        f"; vaseweld: weld boundary at Z={plan.cut_layer.z:.3f}, "
        f"{bottom_role} below / {top_role} above"
    )
    sink.lines.append("G92 E0")
    seam = cursor.copy()
    cursor = state_at(top, plan.top_kept[0].start)
    resume = cursor.copy()
    # The travel goes to the top file's own last position; the nozzle is still
    # wherever the bottom file left it until then.
    seam.x, seam.y = cursor.x, cursor.y

    # A vase layer expects the nozzle to already be on its spiral, because in the
    # source file the previous layer ended there. After a weld it is wherever the
    # other file stopped, so the seam has to travel it back to that point.
    opening = _opening(top.lines[plan.top_kept[0].start : plan.top_kept[0].end], cursor)
    bridge = _seam_lines(top.config, seam, resume, opening, retract=seam_retract)
    sink.append_generated(bridge.at_boundary, cursor)

    for position, layer in enumerate(plan.top_kept):
        lines = top.lines[layer.start : layer.end]
        spans.append((len(sink.lines), len(spans) + 1))
        if position == 0 and bridge.before_printing:
            sink.feed(lines[: opening.printing_at], cursor)
            sink.append_generated(bridge.before_printing, cursor)
            lines = lines[opening.printing_at :]
        factors = (
            _ramp_factors(lines, cursor, ramp_in=True, flow=plan.start_flow)
            if top_role == "vase" and position == 0
            else None
        )
        sink.feed(lines, cursor, factors)

    footer_sink = _Sink()
    footer_sink.feed(top.lines[top.footer_start :], cursor)
    return _Spliced(
        body=sink.lines,
        footer=footer_sink.lines,
        spans=spans,
        seam_start=seam_start,
        net_e=sink.net_e + footer_sink.net_e,
    )


def _finalise(
    bottom: GcodeFile, top: GcodeFile, plan: _Plan, spliced: _Spliced
) -> tuple[str, list[str]]:
    """Fix the counters, progress and totals that a splice invalidates.

    Returns a human-readable note and the list of things that had to be removed
    because they could not be recomputed.
    """
    body, footer = spliced.body, spliced.footer
    removed: list[str] = []
    progress = _build_progress(bottom, plan.bottom_last.end, top, plan.top_kept[0].start)
    _renumber_layers(body, footer, spliced.spans, plan.total_layers)

    if progress is None:
        before = len(body) + len(footer)
        body[:] = [line for line in body if not _M73.match(line)]
        footer[:] = [line for line in footer if not _M73.match(line)]
        note = "M73 progress stripped"
        if before > len(body) + len(footer):
            removed.append("M73 progress reporting")
    else:
        for i, line in enumerate(body):
            if _M73.match(line):
                body[i] = progress.rewrite(line, from_bottom=i < spliced.seam_start)
        for i, line in enumerate(footer):
            if _M73.match(line):
                footer[i] = progress.rewrite(line, from_bottom=False)
        note = "M73 progress remapped"

    seconds = _welded_seconds(bottom, top, progress)
    stats = {
        "seconds": seconds,
        "first_layer_seconds": _time_comment_seconds(
            bottom.lines, "estimated first layer printing time"
        ),
        "material": _material_totals(spliced.net_e, top.config),
    }
    _rewrite_stats(body, **stats)
    _rewrite_stats(footer, **stats)
    if seconds is None:
        if _time_comment_seconds(top.lines, "estimated printing time") is not None:
            removed.append("the printing time estimate")
        return note + ", time estimate stripped", removed
    return f"{note}, time estimate {_format_duration(seconds)}", removed


def weld(
    bottom: GcodeFile,
    top: GcodeFile,
    cut_z: float,
    *,
    bottom_role: str,
    top_role: str,
    start_flow: float | None = None,
    finish_flow: float | None = None,
    seam_retract: bool = True,
) -> WeldResult:
    """Weld ``bottom`` (below the cut) to ``top`` (at and above the cut)."""
    plan = _plan(
        bottom,
        top,
        cut_z,
        bottom_role=bottom_role,
        top_role=top_role,
        start_flow=start_flow,
        finish_flow=finish_flow,
    )
    spliced = _splice(
        bottom, top, plan, bottom_role=bottom_role, top_role=top_role, seam_retract=seam_retract
    )
    stats_note, stats_removed = _finalise(bottom, top, plan, spliced)

    cut_layer, kept_bottom, top_kept = plan.cut_layer, plan.kept_bottom, plan.top_kept
    bottom_last = plan.bottom_last
    banner = _provenance(
        bottom=bottom,
        top=top,
        bottom_role=bottom_role,
        top_role=top_role,
        cut_layer=cut_layer,
        kept_bottom=kept_bottom,
        top_kept=top_kept,
    )
    body = spliced.body
    return WeldResult(
        lines=body[:1] + banner + body[1:] + spliced.footer,
        newline=bottom.newline,
        cut_z=cut_layer.z,
        requested_z=cut_z,
        cut_layer=cut_layer.index,
        bottom_role=bottom_role,
        top_role=top_role,
        bottom_range=(kept_bottom[0].index, bottom_last.index),
        top_range=(top_kept[0].index, top_kept[-1].index),
        bottom_z=(kept_bottom[0].z, bottom_last.z),
        top_z=(top_kept[0].z, top_kept[-1].z),
        e_mode_note=_e_mode_note(bottom, top),
        ramp_note=(
            f"{plan.start_flow:.2f} -> 1.00 over layer {cut_layer.index}"
            if top_role == "vase"
            else f"1.00 -> {plan.finish_flow:.2f} over layer {bottom_last.index}"
        ),
        stats_note=stats_note,
        stats_removed=stats_removed,
        warnings=plan.warnings,
    )
