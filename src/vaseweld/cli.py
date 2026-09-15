"""Command line entry point for vaseweld."""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable

from . import __version__
from .compat import CompatError, check_compatible
from .parser import GcodeError, GcodeFile, parse_file
from .preflight import PreflightError, check_plate
from .preview import write as write_preview
from .slicer import (
    SPIRAL_VASE_OVERRIDES,
    SlicerError,
    dropped_supports,
    find_slicer,
    merged_config,
    normal_overrides,
    probe_slicer,
    run_slice,
    supports_are_on,
    unrecoverable_vase,
    version_complaint,
)
from .validate import check as run_check
from .weld import WeldError, weld

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2

_EPILOG = r"""With PrusaSlicer installed, one command does the whole job:

  vaseweld auto project.3mf --at 12.4 -o out.gcode

Or slice the same plate twice yourself, once with Spiral Vase off and once on, then:

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


def _finite(what: str, *, positive: bool = False) -> "Callable[[str], float]":
    """An argparse type that refuses nan, which slips past any pair of one-sided comparisons."""

    def parse(text: str) -> float:
        try:
            value = float(text)
        except ValueError:
            value = math.nan
        if not math.isfinite(value) or (positive and value <= 0):
            raise argparse.ArgumentTypeError(f"{what}, got {text!r}")
        return value

    return parse


def _add_weld_options(cmd: argparse.ArgumentParser) -> None:
    """Options that mean the same thing to `weld` and to `auto`."""
    cmd.add_argument(
        "--at",
        required=True,
        type=_finite("cut height must be a number of mm"),
        metavar="Z",
        action="append",
        help="cut height in mm; repeat it to alternate again, so two cuts give a "
        "solid base, a vase body and a solid lid",
    )
    cmd.add_argument(
        "--vase-first",
        action="store_true",
        help="start with the vase part below the first cut instead of the normal part",
    )
    cmd.add_argument(
        "--start-flow",
        type=float,
        metavar="RATIO",
        help="flow ratio the vase transition layer ramps up from "
        "(default: spiral_starting_flow_ratio from the vase file, else 0.8)",
    )
    cmd.add_argument(
        "--finish-flow",
        type=float,
        metavar="RATIO",
        help="flow ratio the last vase layer ramps down to "
        "(default: spiral_finishing_flow_ratio from the vase file, else 0.25)",
    )
    cmd.add_argument(
        "--no-seam-retract",
        action="store_true",
        help="do not retract before the seam travel; the retraction state is still matched",
    )
    cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="report the plan and write nothing",
    )
    cmd.add_argument(
        "--force",
        action="store_true",
        help="weld even if the two files disagree on printer or plate settings",
    )


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
    weld_cmd.add_argument("-o", "--output", type=Path, help="file to write")
    weld_cmd.add_argument(
        "gcode",
        nargs="?",
        type=Path,
        help="the file a slicer post-processing hook just produced; it takes whichever "
        "of --normal or --vase you left out, and is rewritten in place unless -o is given",
    )
    _add_weld_options(weld_cmd)

    auto_cmd = sub.add_parser(
        "auto",
        help="slice a project twice with PrusaSlicer and weld the result",
        description="Slice one project twice with PrusaSlicer, normal and spiral vase, "
        "and weld them. Needs PrusaSlicer 2.9.x on the machine.",
    )
    auto_cmd.add_argument(
        "project",
        type=Path,
        help="a .3mf project, or any model file PrusaSlicer opens (.stl, .obj, .step)",
    )
    auto_cmd.add_argument(
        "-o",
        "--output",
        type=Path,
        help="file to write (default: PROJECT-vaseweld.gcode beside the project)",
    )
    _add_weld_options(auto_cmd)
    auto_cmd.add_argument(
        "--load",
        type=Path,
        action="append",
        metavar="INI",
        help="a PrusaSlicer config .ini to slice with; repeat it to layer several. "
        "Required for a bare model file unless PrusaSlicer's built-in defaults will do",
    )
    auto_cmd.add_argument(
        "--slicer-path",
        type=Path,
        metavar="PATH",
        help="the PrusaSlicer binary, if it is not on PATH or in a standard install dir",
    )
    auto_cmd.add_argument(
        "--force-slicer-version",
        action="store_true",
        help="run against a PrusaSlicer version auto is not verified on",
    )
    auto_cmd.add_argument(
        "--slicer-timeout",
        type=_finite("timeout must be a positive number of seconds", positive=True),
        metavar="SECONDS",
        help="give up on a slicing pass after this long (default: wait)",
    )
    auto_cmd.add_argument(
        "--keep-slices",
        type=Path,
        metavar="DIR",
        help="write the two intermediate slices here instead of a temporary directory",
    )
    auto_cmd.add_argument(
        "--verbose",
        action="store_true",
        help="print each PrusaSlicer command line and everything it prints",
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


def _reject_bgcode_output(output: Path) -> None:
    if output.suffix.lower() != ".bgcode":
        return
    raise WeldError(
        f"{output}: vaseweld reads binary G-code but cannot write it. "
        "Give -o a .gcode name, or turn off 'Supports binary G-code' in "
        "Print Settings > Output options on the profile that runs this hook. "
        "Printers that take .bgcode take plain G-code too."
    )


def _reject_unwritable_output(output: Path) -> None:
    """auto spends two slicing runs before it writes anything, so look at the target first."""
    if output.is_dir():
        raise GcodeError(f"{output} is a directory. Give -o a file name.")
    if not output.parent.is_dir():
        raise GcodeError(f"{output}: cannot write (no such directory {output.parent})")


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
    _reject_bgcode_output(output)
    return normal, vase, output


def _run_weld(args: argparse.Namespace, out: "object") -> int:
    _validate_flow("--start-flow", args.start_flow)
    _validate_flow("--finish-flow", args.finish_flow)
    normal_path, vase_path, output = _resolve_inputs(args)
    return _weld_files(args, out, parse_file(normal_path), parse_file(vase_path), output)


def _weld_files(
    args: argparse.Namespace,
    out: "object",
    normal: GcodeFile,
    vase: GcodeFile,
    output: Path,
) -> int:
    """Weld two parsed files and report. Shared by `weld` and `auto`."""
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


def _mode_divergence(normal: GcodeFile, vase: GcodeFile) -> str | None:
    """The two passes are meant to differ in exactly one setting. Check PrusaSlicer agreed."""
    modes = (normal.config.get("spiral_vase"), vase.config.get("spiral_vase"))
    if None in modes or modes == ("0", "1"):
        return None
    if modes == ("1", "0"):
        what = "the normal pass came out as a vase and the spiral vase pass came out normal"
    else:
        both = "as a vase" if modes == ("1", "1") else "with spiral vase off"
        what = f"both passes were sliced {both}"
    return (
        f"{what} (spiral_vase={modes[0]} and {modes[1]}), so there is nothing to weld. "
        "PrusaSlicer did not take the override; run with --verbose to see the command line "
        "it was given."
    )


def _ladder_cause(normal: GcodeFile) -> str:
    """What moved the Zs apart. Supports first: the spiral pass is never sliced with them."""
    if supports_are_on(normal.config):
        return (
            "The normal pass was sliced with support material and the spiral vase pass cannot be, "
            "because PrusaSlicer refuses that combination, and supports move the layer Zs. Turn "
            "supports off for this plate."
        )
    return "Adaptive or variable layer height does this; slice at a fixed layer height."


def _ladder_divergence(normal: GcodeFile, vase: GcodeFile, tail: str = "") -> str | None:
    """Why these two slices cannot be welded, if their Z ladders are not the same one."""
    a, b = _ladder(normal), _ladder(vase)
    if a == b:
        return None
    head = (
        "the two slices disagree about layer Z, so welding them would produce a "
        "plausible-looking file that does not print. "
    )
    for index, (left, right) in enumerate(zip(a, b), start=1):
        if left != right:
            return (
                f"{head}Layer {index} is Z {left:.3f} in the normal pass and "
                f"Z {right:.3f} in the spiral vase pass. "
                f"{_ladder_cause(normal)}{tail}"
            )
    return (
        f"{head}The normal pass has {len(a)} layers and the spiral vase pass {len(b)}, "
        f"agreeing up to the shorter of the two.{tail}"
    )


class _SliceDir:
    """Where the two passes land: a kept directory, or one that is cleaned up."""

    def __init__(self, keep: Path | None) -> None:
        self._keep = keep
        self._temp: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> Path:
        if self._keep is not None:
            try:
                self._keep.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise GcodeError(
                    f"{self._keep}: cannot use for --keep-slices ({exc.strerror or exc})"
                ) from exc
            return self._keep
        # a transient file lock on Windows must not turn a finished weld into a traceback
        self._temp = tempfile.TemporaryDirectory(prefix="vaseweld-", ignore_cleanup_errors=True)
        return Path(self._temp.name)

    def __exit__(self, *exc_info) -> None:
        if self._temp is not None:
            self._temp.cleanup()


def _run_auto(args: argparse.Namespace, out: "object") -> int:
    _validate_flow("--start-flow", args.start_flow)
    _validate_flow("--finish-flow", args.finish_flow)
    project: Path = args.project
    # before the default output name is built from it, because "." has no stem to build one from
    plate = check_plate(project)
    output = args.output or project.with_name(f"{project.stem}-vaseweld.gcode")
    _reject_bgcode_output(output)

    load = tuple(args.load or ())
    for config in load:
        if not config.is_file():
            raise SlicerError(f"{config}: no such config file. Check --load.")

    found = probe_slicer(find_slicer(args.slicer_path))
    complaint = version_complaint(found)
    if complaint is not None:
        if complaint.fatal and not args.force_slicer_version:
            raise SlicerError(f"{complaint.message} Pass --force-slicer-version to run anyway.")
        print(f"warning: {complaint.message}", file=sys.stderr)

    print(f"slicer: {found.path} ({found.version})", file=out)
    print(plate.describe(), file=out)
    echo = out if args.verbose else None

    config = merged_config(project, load)
    for warning in (unrecoverable_vase(config), dropped_supports(config)):
        if warning is not None:
            print(f"warning: {warning}", file=sys.stderr)

    with _SliceDir(args.keep_slices) as workdir:
        # said before the first pass runs, so a pass that fails still says where to look
        if args.keep_slices is not None:
            print(f"keeping both slices in {workdir}", file=out)
        # after the workdir exists, because -o inside --keep-slices is a reasonable thing to ask for
        _reject_unwritable_output(output)
        normal_path = workdir / f"{project.stem}-normal.gcode"
        vase_path = workdir / f"{project.stem}-spiral.gcode"
        for step, (destination, overrides, label) in enumerate(
            (
                (normal_path, normal_overrides(config), "normal"),
                (vase_path, SPIRAL_VASE_OVERRIDES, "spiral vase"),
            ),
            start=1,
        ):
            # a pass takes minutes; say so before blocking, even down a pipe
            print(f"pass {step}/2 {label}", file=out, flush=True)
            run_slice(
                found,
                project,
                destination,
                overrides=overrides,
                load=load,
                timeout=args.slicer_timeout,
                echo=echo,
            )

        normal, vase = parse_file(normal_path), parse_file(vase_path)
        look = (
            f" Both passes are in {workdir}."
            if args.keep_slices is not None
            else " Re-run with --keep-slices to look at both passes."
        )
        divergence = _ladder_divergence(normal, vase, look) or _mode_divergence(normal, vase)
        if divergence is not None:
            raise WeldError(divergence)
        for line in _ladder_report(vase, project.name):
            print(line, file=out)
        return _weld_files(args, out, normal, vase, output)


def _run_preview(args: argparse.Namespace, out: "object") -> int:
    destination = args.output or args.file.with_suffix(".html")
    try:
        written = write_preview(destination, args.file)
    except OSError as exc:
        raise GcodeError(f"{destination}: cannot write ({exc.strerror or exc})") from exc
    size = written.stat().st_size / 1024
    print(f"wrote {written} ({size:.0f} KB), open it in any browser", file=out)
    return EXIT_OK


def _ladder(gcode: GcodeFile) -> list[float]:
    return [layer.z for layer in gcode.layers]


def _ladder_report(gcode: GcodeFile, name: str | None = None) -> list[str]:
    """What `layers` prints about a file, minus the per-layer listing."""
    zs = _ladder(gcode)
    steps = {round(b - a, 4) for a, b in zip(zs, zs[1:])}
    lines = [f"{name or gcode.path.name}: {len(zs)} layers, Z {zs[0]:.3f} to {zs[-1]:.3f}"]
    if len(steps) == 1:
        lines.append(f"layer height: {next(iter(steps)):.3f} mm")
    else:
        lines.append(
            f"layer height: varies, {min(steps):.3f} to {max(steps):.3f} mm. "
            "Slice both files at a fixed layer height before welding."
        )
    lines.append(f"weldable range: Z {zs[1]:.3f} to {zs[-1]:.3f} (layers 2 to {len(zs)})")
    return lines


def _run_layers(args: argparse.Namespace, out: "object") -> int:
    gcode = parse_file(args.file)
    for line in _ladder_report(gcode):
        print(line, file=out)
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
        if args.command == "auto":
            return _run_auto(args, out)
        if args.command == "layers":
            return _run_layers(args, out)
        if args.command == "preview":
            return _run_preview(args, out)
        return _run_check(args, out)
    except (GcodeError, CompatError, WeldError, PreflightError, SlicerError) as exc:
        print(f"vaseweld: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("vaseweld: interrupted", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
