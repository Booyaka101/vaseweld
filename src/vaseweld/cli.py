"""Command line entry point for vaseweld."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .compat import CompatError, check_compatible
from .parser import GcodeError, parse_file
from .preview import write as write_preview
from .validate import check as run_check
from .weld import WeldError, weld

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2

_EPILOG = r"""Slice the same plate twice, once with Spiral Vase off and once on, then:

  vaseweld layers body.gcode
  vaseweld weld --normal base.gcode --vase body.gcode --at 12.4 -o out.gcode
  vaseweld check out.gcode
  vaseweld preview out.gcode

Repeat --at to alternate again, so a solid base, a vase body and a solid lid is:

  vaseweld weld --normal base.gcode --vase body.gcode --at 12.4 --at 30 -o out.gcode

The output always uses relative extrusion (M83). Absolute-E inputs are converted.

As a slicer post-processing script, leave out the side the slicer is producing and
let it append the temporary file path:

  vaseweld weld --normal C:\prints\base.gcode --at 12.4
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaseweld",
        description="Weld two G-code files sliced from the same plate at a chosen Z height.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"vaseweld {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    weld_cmd = sub.add_parser(
        "weld",
        help="splice a normal slice and a vase slice at a Z height",
        description="Splice a normal slice and a spiral vase slice at a Z height.",
    )
    weld_cmd.add_argument("--normal", type=Path, help="the non-vase slice")
    weld_cmd.add_argument("--vase", type=Path, help="the spiral vase slice")
    weld_cmd.add_argument(
        "--at",
        required=True,
        type=float,
        metavar="Z",
        action="append",
        help="cut height in mm; repeat it to alternate again, so two cuts give a "
        "solid base, a vase body and a solid lid",
    )
    weld_cmd.add_argument("-o", "--output", type=Path, help="file to write")
    weld_cmd.add_argument(
        "gcode",
        nargs="?",
        type=Path,
        help="the file a slicer post-processing hook just produced; it takes whichever "
        "of --normal or --vase you left out, and is rewritten in place unless -o is given",
    )
    weld_cmd.add_argument(
        "--vase-first",
        action="store_true",
        help="start with the vase part below the first cut instead of the normal part",
    )
    weld_cmd.add_argument(
        "--start-flow",
        type=float,
        metavar="RATIO",
        help="flow ratio the vase transition layer ramps up from "
        "(default: spiral_starting_flow_ratio from the vase file, else 0.8)",
    )
    weld_cmd.add_argument(
        "--finish-flow",
        type=float,
        metavar="RATIO",
        help="flow ratio the last vase layer ramps down to "
        "(default: spiral_finishing_flow_ratio from the vase file, else 0.25)",
    )
    weld_cmd.add_argument(
        "--no-seam-retract",
        action="store_true",
        help="do not retract before the seam travel; the retraction state is still matched",
    )
    weld_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="report the plan and write nothing",
    )
    weld_cmd.add_argument(
        "--force",
        action="store_true",
        help="weld even if the two files disagree on printer or plate settings",
    )

    preview_cmd = sub.add_parser(
        "preview",
        help="write a self-contained HTML page you can open and scrub through",
        description="Draw the toolpath into one HTML file: no server, no dependencies.",
    )
    preview_cmd.add_argument("file", type=Path, help="G-code file to draw")
    preview_cmd.add_argument("-o", "--output", type=Path, help="defaults to FILE.html")

    layers_cmd = sub.add_parser(
        "layers",
        help="list the layer heights a file can be cut at",
        description="Report the Z ladder, so you can pick a cut without guessing.",
    )
    layers_cmd.add_argument("file", type=Path, help="G-code file to inspect")
    layers_cmd.add_argument(
        "--all", action="store_true", help="print every layer, not just the summary"
    )

    check_cmd = sub.add_parser(
        "check",
        help="verify a G-code file is coherent enough to print",
        description="Verify Z, extrusion, retractions and the temperature timeline.",
    )
    check_cmd.add_argument("file", type=Path, help="G-code file to check")
    return parser


def _validate_flow(name: str, value: float | None) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise WeldError(f"{name} must be between 0 and 1, got {value}")


def _resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """Work out the two inputs and the destination, honouring a trailing hook file."""
    normal, vase, output = args.normal, args.vase, args.output
    if args.gcode is not None:
        if normal is not None and vase is not None:
            raise WeldError(
                f"--normal and --vase are both set, so {args.gcode} has no role. "
                "Drop one of them when a slicer passes the file it just wrote."
            )
        if normal is None:
            normal = args.gcode
        else:
            vase = args.gcode
        output = output or args.gcode
    if normal is None or vase is None:
        raise WeldError(
            "need both slices: pass --normal and --vase, or pass one of them plus the "
            "file a slicer post-processing hook hands over as the last argument"
        )
    if output is None:
        raise WeldError("no destination: pass -o/--output")
    if output.suffix.lower() == ".bgcode":
        raise WeldError(
            f"{output}: vaseweld reads binary G-code but cannot write it. "
            "Give -o a .gcode name, or turn off 'Supports binary G-code' in "
            "Print Settings > Output options on the profile that runs this hook. "
            "Printers that take .bgcode take plain G-code too."
        )
    return normal, vase, output


def _run_weld(args: argparse.Namespace, out: "object") -> int:
    _validate_flow("--start-flow", args.start_flow)
    _validate_flow("--finish-flow", args.finish_flow)
    normal_path, vase_path, output = _resolve_inputs(args)

    normal = parse_file(normal_path)
    vase = parse_file(vase_path)
    try:
        check_compatible(normal, vase)
    except CompatError as exc:
        if not args.force:
            raise
        print(f"warning: {exc}", file=sys.stderr)
        print("warning: continuing because --force was given", file=sys.stderr)

    result = weld(
        normal,
        vase,
        args.at,
        first_role="vase" if args.vase_first else "normal",
        start_flow=args.start_flow,
        finish_flow=args.finish_flow,
        seam_retract=not args.no_seam_retract,
    )

    if not args.dry_run:
        text = result.newline.join(result.lines) + result.newline
        try:
            output.write_text(text, encoding="utf-8", newline="")
        except OSError as exc:
            raise GcodeError(f"{output}: cannot write ({exc.strerror or exc})") from exc

    for line in result.summary():
        print(line, file=out)
    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {output} ({len(result.lines)} lines)", file=out)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if result.stats_removed:
        print(
            "note: removed "
            + " and ".join(result.stats_removed)
            + ", which cannot be recomputed from these two files",
            file=sys.stderr,
        )
    destination = os.environ.get("SLIC3R_PP_OUTPUT_NAME")
    if destination:
        print(f"slicer will save it as {destination}", file=out)
    return EXIT_OK


def _run_preview(args: argparse.Namespace, out: "object") -> int:
    destination = args.output or args.file.with_suffix(".html")
    try:
        written = write_preview(destination, args.file)
    except OSError as exc:
        raise GcodeError(f"{destination}: cannot write ({exc.strerror or exc})") from exc
    size = written.stat().st_size / 1024
    print(f"wrote {written} ({size:.0f} KB), open it in any browser", file=out)
    return EXIT_OK


def _run_layers(args: argparse.Namespace, out: "object") -> int:
    gcode = parse_file(args.file)
    zs = [layer.z for layer in gcode.layers]
    steps = {round(b - a, 4) for a, b in zip(zs, zs[1:])}
    print(f"{gcode.path.name}: {len(zs)} layers, Z {zs[0]:.3f} to {zs[-1]:.3f}", file=out)
    if len(steps) == 1:
        print(f"layer height: {next(iter(steps)):.3f} mm", file=out)
    else:
        print(
            f"layer height: varies, {min(steps):.3f} to {max(steps):.3f} mm. "
            "Slice both files at a fixed layer height before welding.",
            file=out,
        )
    print(
        f"weldable range: Z {zs[1]:.3f} to {zs[-1]:.3f} (layers 2 to {len(zs)})",
        file=out,
    )
    if args.all:
        for layer in gcode.layers:
            print(f"  layer {layer.index:4d}  Z {layer.z:.3f}", file=out)
    return EXIT_OK


def _run_check(args: argparse.Namespace, out: "object") -> int:
    report = run_check(args.file)
    print(report.summary(), file=out)
    for problem in report.problems:
        print(problem, file=out)
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def main(argv: list[str] | None = None, out: "object" = None) -> int:
    out = out or sys.stdout
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "weld":
            return _run_weld(args, out)
        if args.command == "layers":
            return _run_layers(args, out)
        if args.command == "preview":
            return _run_preview(args, out)
        return _run_check(args, out)
    except (GcodeError, CompatError, WeldError) as exc:
        print(f"vaseweld: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("vaseweld: interrupted", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
