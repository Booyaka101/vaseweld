"""Drive PrusaSlicer's command line so `auto` can produce both slices itself.

Only PrusaSlicer. OrcaSlicer and BambuStudio expose different CLI surfaces and are
still supported as *input* to `weld`, just not driven from here.

The spiral vase pass carries the full companion override set rather than
``--spiral-vase`` alone. That set is copied from PrusaSlicer's own GUI
(``src/slic3r/GUI/ConfigManipulation.cpp``), which applies it from inside a
``MessageDialog`` that never runs headless. Measured against 2.9.6, three of the
six are redundant, two turn a hard slicing error into a working slice, and one
changes the toolpath in silence. See SPIRAL_VASE_OVERRIDES.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

DOWNLOAD_URL = "https://www.prusa3d.com/prusaslicer/"

# Copied from PrusaSlicer 2.9.6 src/slic3r/GUI/ConfigManipulation.cpp:158-163, the only
# place this set is authoritative. Against 2.9.6 the three keys libslic3r already forces
# in DynamicPrintConfig::normalize_fdm (perimeters, top_solid_layers, fill_density) are
# redundant; support_material and support_material_enforce_layers stop
# PrintConfig's validate() refusing the slice outright when the user's profile enables
# them; thin_walls is the one key nothing upstream corrects, and leaving it on moves the
# toolpath. Passing all six keeps us right whichever of those PrusaSlicer changes.
SPIRAL_VASE_OVERRIDES = (
    "--spiral-vase=1",
    "--perimeters=1",
    "--top-solid-layers=0",
    "--fill-density=0",
    "--support-material=0",
    "--support-material-enforce-layers=0",
    "--thin-walls=0",
)

VERIFIED_SERIES = (2, 9)
REFACTORED_SERIES = (3, 0)

_BANNER = re.compile(r"PrusaSlicer-(\d+)\.(\d+)\.(\d+)(\S*)")
_EXE_NAMES = ("prusa-slicer-console.exe", "prusa-slicer", "prusa-slicer.exe", "PrusaSlicer")
_PROBE_TIMEOUT = 60.0


class SlicerError(Exception):
    """PrusaSlicer is missing, is the wrong version, or refused to slice."""


@dataclass(frozen=True)
class Slicer:
    """A located PrusaSlicer binary and whatever it says about itself."""

    path: Path
    banner: str
    release: tuple[int, int, int] | None = None
    suffix: str = ""

    @property
    def version(self) -> str:
        if self.release is None:
            return "unknown version"
        return ".".join(str(n) for n in self.release) + self.suffix

    @property
    def series(self) -> tuple[int, int] | None:
        return None if self.release is None else self.release[:2]


def _windows_candidates() -> list[Path]:
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramW6432", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    ]
    found: list[Path] = []
    for root in roots:
        if not root:
            continue
        base = Path(root)
        for pattern in ("Prusa3D/PrusaSlicer*", "PrusaSlicer*"):
            for directory in sorted(base.glob(pattern), reverse=True):
                found.append(directory / "prusa-slicer-console.exe")
                found.append(directory / "prusa-slicer.exe")
    return found


def _macos_candidates() -> list[Path]:
    apps = [Path("/Applications"), Path.home() / "Applications"]
    names = ["PrusaSlicer.app", "Original Prusa Drivers/PrusaSlicer.app"]
    return [base / name / "Contents/MacOS/PrusaSlicer" for base in apps for name in names]


def _linux_candidates() -> list[Path]:
    home = Path.home()
    fixed = [
        Path("/usr/bin/prusa-slicer"),
        Path("/usr/local/bin/prusa-slicer"),
        Path("/opt/PrusaSlicer/prusa-slicer"),
        Path("/var/lib/flatpak/exports/bin/com.prusa3d.PrusaSlicer"),
        home / ".local/share/flatpak/exports/bin/com.prusa3d.PrusaSlicer",
        home / ".local/bin/prusa-slicer",
    ]
    images = sorted((home / "Applications").glob("PrusaSlicer*.AppImage"), reverse=True)
    return fixed + list(images)


def slicer_candidates() -> list[Path]:
    """Standard install locations for this platform, most likely first."""
    if sys.platform == "win32":
        found = _windows_candidates()
    elif sys.platform == "darwin":
        found = _macos_candidates()
    else:
        found = _linux_candidates()
    return list(dict.fromkeys(found))


def find_slicer(explicit: Path | str | None = None) -> Path:
    """Locate the PrusaSlicer binary: --slicer-path, then PATH, then install dirs."""
    if explicit is not None:
        path = Path(explicit)
        if path.is_dir():
            for name in _EXE_NAMES:
                if (path / name).is_file():
                    return path / name
            raise SlicerError(
                f"{path} is a directory with no PrusaSlicer binary in it. "
                f"Point --slicer-path at one of {', '.join(_EXE_NAMES)}."
            )
        if not path.is_file():
            raise SlicerError(f"{path}: no such file. Check --slicer-path.")
        return path

    for name in _EXE_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)

    for candidate in slicer_candidates():
        if candidate.is_file():
            return candidate

    raise SlicerError(
        f"PrusaSlicer not found. Pass --slicer-path, or install it from {DOWNLOAD_URL}"
    )


def _capture(argv: list[str], timeout: float | None) -> subprocess.CompletedProcess:
    """Run PrusaSlicer headless. Both callers need the same stdin and decoding."""
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


def probe_slicer(path: Path) -> Slicer:
    """Read the version banner. PrusaSlicer has no --version; --help prints it."""
    try:
        done = _capture([str(path), "--help"], _PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SlicerError(
            f"{path} did not answer --help within {_PROBE_TIMEOUT:g}s. "
            "Check --slicer-path points at PrusaSlicer."
        ) from None
    except OSError as exc:
        raise SlicerError(f"{path}: cannot run it ({exc.strerror or exc})") from exc

    banner = ""
    for line in (done.stdout + "\n" + done.stderr).splitlines():
        if line.strip():
            banner = line.strip()
            break

    match = _BANNER.search(done.stdout) or _BANNER.search(done.stderr)
    if match is None:
        return Slicer(path=path, banner=banner)
    release = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return Slicer(path=path, banner=banner, release=release, suffix=match.group(4))


class VersionComplaint(NamedTuple):
    """Why this build is not one `auto` is verified on, and whether that stops us."""

    message: str
    fatal: bool


def version_complaint(found: Slicer) -> VersionComplaint | None:
    """The one line to print when this build is not a version `auto` is verified on."""
    if found.release is None:
        return VersionComplaint(_unidentified(found), fatal=True)
    if found.release[:2] >= REFACTORED_SERIES:
        return VersionComplaint(
            f"PrusaSlicer {found.version} rewrote its command line argument parsing "
            f'(3.0.0-alpha11: "The whole code for parsing and evaluating the command line '
            f'arguments was completely refactored"), so vaseweld auto is unverified there.',
            fatal=True,
        )
    if found.series != VERIFIED_SERIES:
        return VersionComplaint(
            f"PrusaSlicer {found.version} is older than the "
            f"{VERIFIED_SERIES[0]}.{VERIFIED_SERIES[1]}.x that vaseweld auto is verified against.",
            fatal=False,
        )
    return None


def _unidentified(found: Slicer) -> str:
    said = f"it said {found.banner!r}" if found.banner else "it printed nothing"
    hint = ""
    # prusa-slicer.exe is a GUI subsystem binary: it runs, says nothing, and looks broken.
    if sys.platform == "win32" and found.path.name.lower() == "prusa-slicer.exe":
        hint = " On Windows, point --slicer-path at prusa-slicer-console.exe instead."
    return (
        f"{found.path} does not identify itself as PrusaSlicer ({said}). "
        f"vaseweld auto drives PrusaSlicer "
        f"{VERIFIED_SERIES[0]}.{VERIFIED_SERIES[1]}.x only.{hint}"
    )


def slicer_argv(
    found: Slicer,
    source: Path,
    destination: Path,
    *,
    overrides: tuple[str, ...] = (),
    load: tuple[Path, ...] = (),
) -> list[str]:
    """The exact command line one pass runs. Asserted verbatim by the tests."""
    argv = [str(found.path), "--export-gcode"]
    for config in load:
        argv += ["--load", str(config)]
    argv += list(overrides)
    argv += ["--output", str(destination), str(source)]
    return argv


def quoted(argv: list[str]) -> str:
    """A command line the user can paste back into their own shell."""
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def run_slice(
    found: Slicer,
    source: Path,
    destination: Path,
    *,
    overrides: tuple[str, ...] = (),
    load: tuple[Path, ...] = (),
    timeout: float | None = None,
    echo: "object" = None,
) -> Path:
    """Run one pass. Raises SlicerError unless `destination` exists afterwards."""
    argv = slicer_argv(found, source, destination, overrides=overrides, load=load)
    if echo is not None:
        print(f"  $ {quoted(argv)}", file=echo)
    try:
        done = _capture(argv, timeout)
    except subprocess.TimeoutExpired:
        raise SlicerError(
            f"PrusaSlicer did not finish within {timeout:g}s. "
            "Raise --slicer-timeout, or slice by hand and use `vaseweld weld`."
        ) from None
    except OSError as exc:
        raise SlicerError(f"{found.path}: cannot run it ({exc.strerror or exc})") from exc

    if echo is not None:
        for stream in (done.stdout, done.stderr):
            for line in stream.splitlines():
                if line.strip():
                    print(f"  {line.rstrip()}", file=echo)

    # 2.9.6 exits 0 after "All objects are outside of the print volume" and writes
    # nothing, so the return code alone cannot tell us whether a pass worked.
    if not destination.is_file():
        raise SlicerError(_slice_failure_message(found, source, done))
    return destination


def _slice_failure_message(found: Slicer, source: Path, done: subprocess.CompletedProcess) -> str:
    detail = _last_meaningful_line(done.stderr) or _last_meaningful_line(done.stdout)
    head = f"PrusaSlicer produced no G-code for {source.name} (exit {done.returncode})"
    if not detail:
        return f"{head}. It printed nothing; run {found.path} on the file by hand to see why."
    return f"{head}: {detail}"


def _last_meaningful_line(text: str) -> str:
    """PrusaSlicer's reason is the last non-progress line it printed."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line and not re.match(r"^\d+\s*=>", line):
            return line
    return ""
