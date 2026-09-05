"""Measure what each case actually deposits on the first printing move after the cut.

Klipper's planner catches a splice that asks for impossible extrusion, but it cannot
see a move that lays too little material over too much distance. That is the other
failure mode of a hand splice, so it is measured here.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vaseweld.parser import parse_file, walk  # noqa: E402

from build_cases import CUT_LAYER, PAIRS  # noqa: E402

_MIN_TRAVEL_MM = 0.05


def first_print_after(path: Path, seam_index: int):
    """(distance, filament, ratio, line) of the first printing move at or past the seam."""
    gcode = parse_file(path)
    ratios: list[float] = []
    found = None
    for step in walk(gcode.lines, gcode.cursor()):
        if step.kind != "move" or step.delta is None:
            continue
        if step.delta <= 0 or step.dist <= _MIN_TRAVEL_MM:
            continue
        ratios.append(step.delta / step.dist)
        if found is None and step.index >= seam_index:
            found = (step.dist, step.delta, step.delta / step.dist, step.line.strip())
    return statistics.median(ratios), found


def seam_index(path: Path, fallback: int) -> int:
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if "vaseweld: weld boundary" in line:
            return i
    return fallback


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gcode", type=Path, default=Path(__file__).parent / "gcode")
    args = ap.parse_args()

    print(f"{'case':34s} {'travel':>9s} {'filament':>10s} {'flow vs normal':>16s}")
    print("-" * 73)
    for mode, (normal_name, _) in PAIRS.items():
        paste_at = parse_file(ROOT / "tests" / "fixtures" / normal_name).layers[CUT_LAYER - 1].start
        for prefix in ("2_%s_naive_text_editor", "3_%s_vaseweld"):
            path = args.gcode / f"{prefix % mode}.gcode"
            if not path.exists():
                continue
            median, found = first_print_after(path, seam_index(path, paste_at))
            if found is None:
                continue
            distance, filament, ratio, _ = found
            print(
                f"{path.stem:34s} {distance:7.3f} mm {filament:8.5f} mm "
                f"{ratio / median * 100:14.1f}%"
            )
    print("-" * 73)
    print("A hand splice leaves the nozzle where the other file stopped, so its first move")
    print("crosses the part. vaseweld travels there first, so its first move is short and")
    print("lays the flow ratio the transition ramp starts from.")


if __name__ == "__main__":
    main()
