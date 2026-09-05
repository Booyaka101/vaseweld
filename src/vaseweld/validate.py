"""Sanity-check a G-code file, welded or not, before it goes near a printer.

The four checks are the ones that catch a bad splice: Z that steps backwards
mid-print, extrusion values that no longer make sense as relative deltas,
retractions that no longer pair up, and a second start-up temperature sequence
stranded in the middle of the file.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .parser import GcodeFile, parse_file, parse_move, strip_comment, walk

_WAIT_TEMP = re.compile(r"^\s*M(109|190)\b", re.IGNORECASE)
_TEMP_S = re.compile(r"\b[SR](\d+(?:\.\d+)?)")

# An extrusion of 25x the file's own median mm-of-filament-per-mm-of-travel is
# not a thick line, it is an absolute E value that leaked into a relative file.
_E_RATIO_LIMIT = 25.0
_MIN_RATIO_SAMPLES = 20
_MIN_TRAVEL_MM = 0.05
_MAX_RETRACT_MM = 20.0
# A healthy file never has more than one retraction's worth of filament pulled
# back at a time. Twice that means an unretract went missing.
_RETRACT_PEAK_LIMIT = 1.5
_RETRACT_TROUGH_LIMIT = -0.5


@dataclass
class Problem:
    line: int
    check: str
    message: str

    def __str__(self) -> str:
        return f"  line {self.line}: {self.message}"


@dataclass
class Report:
    path: Path
    relative_e: bool
    temperature_timelines: int = 1
    retract_length: float = 0.0
    peak_retracted: float = 0.0
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        mode = "relative" if self.relative_e else "absolute"
        plural = "" if self.temperature_timelines == 1 else "s"
        if self.ok:
            return (
                f"OK: Z monotonic, E coherent ({mode}), retractions balanced, "
                f"{self.temperature_timelines} temperature timeline{plural}"
            )
        count = len(self.problems)
        return f"FAIL: {count} problem{'' if count == 1 else 's'} in {self.path.name}"


def _body(gcode: GcodeFile) -> list[str]:
    return gcode.lines[gcode.header_end : gcode.footer_start]


def _check_z(gcode: GcodeFile, report: Report) -> None:
    """Extruding moves may never drop below a height already printed."""
    highest = None
    for offset, line in enumerate(_body(gcode)):
        move = parse_move(line)
        if move is None or move.z is None or not move.e:
            continue  # travel and z-hop may legitimately go down
        if highest is not None and move.z < highest - 1e-6:
            report.problems.append(
                Problem(
                    gcode.header_end + offset + 1,
                    "z",
                    f"Z goes backwards while extruding ({highest:.3f} -> {move.z:.3f})",
                )
            )
            return
        highest = move.z if highest is None else max(highest, move.z)


def _check_extrusion(gcode: GcodeFile, report: Report) -> None:
    """Decode the body's extrusion and record mode switches, blowouts and retractions."""
    cursor = gcode.cursor()
    ratios: list[tuple[int, float, float]] = []
    for step in walk(_body(gcode), cursor):
        number = gcode.header_end + step.index + 1
        if step.kind == "mode":
            if cursor.relative_e != gcode.relative_e:
                report.problems.append(
                    Problem(
                        number,
                        "e",
                        f"extruder mode switches to "
                        f"{'relative' if cursor.relative_e else 'absolute'} "
                        "in the middle of the print",
                    )
                )
            continue
        if step.delta is None:
            continue
        if step.dist > _MIN_TRAVEL_MM:
            if step.delta > 0:
                ratios.append((number, step.delta / step.dist, step.delta))
            continue
        if step.dist:
            continue  # a sub-0.05 mm move is neither a printing line nor a retraction
        if abs(step.delta) > _MAX_RETRACT_MM:
            report.problems.append(
                Problem(
                    number,
                    "e",
                    f"E jumps by {step.delta:.3f} mm with no XY movement; that is not a retraction",
                )
            )
    _check_extrusion_rate(ratios, report)


def _check_extrusion_rate(ratios: list[tuple[int, float, float]], report: Report) -> None:
    if len(ratios) < _MIN_RATIO_SAMPLES:
        return
    median = statistics.median(ratio for _, ratio, _ in ratios)
    limit = median * _E_RATIO_LIMIT
    for number, ratio, delta in ratios:
        if ratio > limit:
            report.problems.append(
                Problem(
                    number,
                    "e",
                    f"extrudes {delta:.5f} mm over {delta / ratio:.3f} mm of travel "
                    f"({ratio:.3f} mm/mm against a file median of {median:.3f}); "
                    "an absolute E value in a relative file looks like this",
                )
            )
            return


def _retract_length(gcode: GcodeFile, largest: float) -> float:
    """The file's configured retraction, or the biggest one it actually performs."""
    for key in ("retract_length", "retraction_length"):
        for piece in gcode.config.get(key, "").split(","):
            try:
                value = float(piece.strip())
            except ValueError:
                continue
            if value > 0:
                return value
    return largest


def _check_retractions(gcode: GcodeFile, report: Report) -> None:
    """Retraction is a running state, not a line count.

    Slicers disagree about how a retraction is spelled: PrusaSlicer uses one
    E-only move, BambuStudio splits it between a wiping move and an E-only tail.
    Tracking how much filament is pulled back at any moment works for both.
    """
    cursor = gcode.cursor()
    peak = trough = 0.0
    peak_at = trough_at = gcode.header_end
    largest = 0.0
    for step in walk(_body(gcode), cursor):
        if step.kind != "move" or not step.delta:
            continue
        if step.delta < 0:
            largest = max(largest, -step.delta)
        elif step.dist:
            continue
        if cursor.retracted > peak:
            peak, peak_at = cursor.retracted, gcode.header_end + step.index + 1
        if cursor.retracted < trough:
            trough, trough_at = cursor.retracted, gcode.header_end + step.index + 1

    length = _retract_length(gcode, largest)
    report.retract_length = length
    report.peak_retracted = peak
    if length <= 0:
        return
    if peak > length * _RETRACT_PEAK_LIMIT:
        report.problems.append(
            Problem(
                peak_at,
                "retract",
                f"retractions do not balance: {peak:.3f} mm pulled back at once against a "
                f"retraction length of {length:.3f} mm, so an unretract is missing",
            )
        )
    if trough < length * _RETRACT_TROUGH_LIMIT:
        report.problems.append(
            Problem(
                trough_at,
                "retract",
                f"retractions do not balance: {-trough:.3f} mm primed past the retracted "
                f"state, so there is an unretract with no retract",
            )
        )


def _check_temperature(gcode: GcodeFile, report: Report) -> None:
    """A blocking heat-and-wait between the first and last extrusion is a second start-up."""
    body = _body(gcode)
    printing = [
        offset
        for offset, line in enumerate(body)
        if (move := parse_move(line)) and move.e and (move.x is not None or move.y is not None)
    ]
    if not printing:
        return
    for offset in range(printing[0], printing[-1] + 1):
        code, _ = strip_comment(body[offset])
        if not _WAIT_TEMP.match(code):
            continue
        value = _TEMP_S.search(code)
        if value is None or float(value.group(1)) <= 0:
            continue
        report.temperature_timelines += 1
        report.problems.append(
            Problem(
                gcode.header_end + offset + 1,
                "temperature",
                f"blocking temperature wait ({code.strip()}) in the middle of the print; "
                "this is the start-up sequence of a second file",
            )
        )


def check(path: str | Path) -> Report:
    """Run every check over one file. Raises GcodeError if it cannot be parsed."""
    gcode = parse_file(path)
    report = Report(path=gcode.path, relative_e=gcode.relative_e)
    _check_z(gcode, report)
    _check_extrusion(gcode, report)
    _check_retractions(gcode, report)
    _check_temperature(gcode, report)
    report.problems.sort(key=lambda problem: problem.line)
    return report
