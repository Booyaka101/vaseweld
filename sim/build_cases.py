"""Generate the G-code cases the Klipper simulation compares.

For each direction it writes four files: the two slicer originals, the splice a
person gets by pasting the two files together in a text editor, and the vaseweld
weld. `run.sh` then feeds all of them to Klipper's own motion planner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vaseweld.parser import parse_file  # noqa: E402
from vaseweld.weld import weld  # noqa: E402

CUT_Z = 3.0
CUT_LAYER = 15  # Z 3.0 at 0.2 mm layers
SECOND_CUT_Z = 4.6  # layer 23, for the solid base / vase body / solid lid case

PAIRS = {
    "absolute": (
        "klipper_absolute_normal_6mm.gcode",
        "klipper_absolute_vase_6mm.gcode",
    ),
    "relative": ("klipper_normal_6mm.gcode", "klipper_vase_6mm.gcode"),
}


def naive_splice(bottom, top) -> list[str]:
    """What a text editor gives you: keep one file up to the cut, paste the other on.

    No extruder mode fix, no G92 reset, no seam, no flow ramp. This is the workaround
    the slicer feature requests describe people using today.
    """
    return (
        bottom.lines[: bottom.layers[CUT_LAYER - 1].start]
        + top.lines[top.layers[CUT_LAYER - 1].start :]
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", type=Path, default=Path(__file__).parent / "gcode")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for stale in args.out.glob("*.gcode"):
        stale.unlink()

    def write(name: str, lines: list[str]) -> None:
        (args.out / f"{name}.gcode").write_text(
            "\n".join(lines) + "\n", encoding="utf-8", newline=""
        )
        print(f"{name:34s} {len(lines):6d} lines")

    for mode, (normal_name, vase_name) in PAIRS.items():
        normal = parse_file(ROOT / "tests" / "fixtures" / normal_name)
        vase = parse_file(ROOT / "tests" / "fixtures" / vase_name)
        write(f"1_{mode}_source_normal", normal.lines)
        write(f"1_{mode}_source_vase", vase.lines)
        write(f"2_{mode}_naive_text_editor", naive_splice(normal, vase))
        write(f"3_{mode}_vaseweld", weld(normal, vase, CUT_Z).lines)
        write(
            f"4_{mode}_vaseweld_vase_first",
            weld(normal, vase, CUT_Z, first_role="vase").lines,
        )
        write(
            f"5_{mode}_vaseweld_two_cuts",
            weld(normal, vase, [CUT_Z, SECOND_CUT_Z]).lines,
        )


if __name__ == "__main__":
    main()
