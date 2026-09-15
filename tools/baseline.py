"""Record `weld` and `check` over the whole fixture matrix, for refactor diffing.

    python tools/baseline.py baseline-before
    ... refactor ...
    python tools/baseline.py baseline-after
    diff -r baseline-before baseline-after

Every welded file, every stdout and every stderr lands in the directory, so a
refactor that changes any of them shows up as a byte difference rather than an
argument.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vaseweld.cli import main  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
PAIRS = [
    ("prusaslicer", "prusaslicer_normal_40mm.gcode", "prusaslicer_vase_40mm.gcode"),
    ("orcaslicer", "orcaslicer_normal_40mm.gcode", "orcaslicer_vase_40mm.gcode"),
    ("bambustudio", "bambustudio_normal_40mm.gcode", "bambustudio_vase_40mm.gcode"),
    ("small", "prusaslicer_normal_6mm.gcode", "prusaslicer_vase_6mm.gcode"),
    ("klipper", "klipper_normal_6mm.gcode", "klipper_vase_6mm.gcode"),
    ("binary", "binary_6mm.bgcode", "binary_vase_6mm.bgcode"),
]
SOLO = [
    "arcfit_normal_6mm.gcode",
    "mismatch_layerheight_6mm.gcode",
    "shifted_placement_1mm.gcode",
    "two_objects_1mm.gcode",
]


def record(out_dir: Path, name: str, argv: list[str]) -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(stderr):
        try:
            code = main(argv, out=stdout)
        except SystemExit as exc:  # argparse usage errors
            code = int(exc.code or 0)
    body = (
        f"$ vaseweld {' '.join(argv)}\nexit {code}\n"
        f"--- stdout\n{stdout.getvalue()}--- stderr\n{stderr.getvalue()}"
    )
    # Without this, comparing two baseline runs compares their directory names.
    for spelling in {
        str(out_dir),
        str(out_dir.resolve()),
        str(out_dir.resolve()).replace("\\", "/"),
    }:
        body = body.replace(spelling, "<out>")
    (out_dir / f"{name}.txt").write_text(body, encoding="utf-8", newline="\n")


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    out_dir = args.directory
    out_dir.mkdir(parents=True, exist_ok=True)

    for label, normal, vase in PAIRS:
        for first in ("normal", "vase"):
            for cuts in (["3"], ["2", "4"]):
                tag = f"{label}_{first}first_at{'_'.join(cuts)}"
                welded = out_dir / f"{tag}.gcode"
                argv_weld = [
                    "weld",
                    "--normal",
                    str(FIXTURES / normal),
                    "--vase",
                    str(FIXTURES / vase),
                ]
                for cut in cuts:
                    argv_weld += ["--at", cut]
                if first == "vase":
                    argv_weld.append("--vase-first")
                argv_weld += ["-o", str(welded)]
                record(out_dir, f"weld_{tag}", argv_weld)
                if welded.exists():
                    record(out_dir, f"check_{tag}", ["check", str(welded)])

    for name in SOLO:
        stem = Path(name).stem
        record(out_dir, f"layers_{stem}", ["layers", str(FIXTURES / name), "--all"])
        record(out_dir, f"check_{stem}", ["check", str(FIXTURES / name)])
        record(
            out_dir,
            f"weldfail_{stem}",
            [
                "weld",
                "--normal",
                str(FIXTURES / name),
                "--vase",
                str(FIXTURES / "prusaslicer_vase_6mm.gcode"),
                "--at",
                "3",
                "-o",
                str(out_dir / f"{stem}_attempt.gcode"),
            ],
        )

    for label, normal, vase in PAIRS:
        for name in (normal, vase):
            record(out_dir, f"layers_{Path(name).stem}", ["layers", str(FIXTURES / name), "--all"])

    print(f"wrote {len(list(out_dir.iterdir()))} files to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
