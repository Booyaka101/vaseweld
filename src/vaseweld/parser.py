"""Split a sliced G-code file into header, layers and footer, and read its config block.

Layer boundaries come from the ``;LAYER_CHANGE`` / ``;Z:`` marker pair that
PrusaSlicer, OrcaSlicer and BambuStudio all emit. Files without those markers
fall back to detecting bare upward Z moves.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .bgcode import BgcodeError, decode, is_bgcode


_LAYER_CHANGE = re.compile(r"^\s*;\s*(LAYER_CHANGE|CHANGE_LAYER)\s*$", re.IGNORECASE)
_Z_MARKER = re.compile(r"^\s*;\s*(?:Z|Z_HEIGHT)\s*:\s*(-?\d*\.?\d+)\s*$", re.IGNORECASE)
_CURA_LAYER = re.compile(r"^\s*;\s*LAYER:\s*-?\d+\s*$", re.IGNORECASE)
_CONFIG_KV = re.compile(r"^\s*;\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_WORD = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")
_PRINTING_OBJECT = re.compile(
    r";\s*(?:start|stop)?\s*printing object.*?\bid:(\d+)\s+copy\s+(\d+)", re.IGNORECASE
)
_EXCLUDE_OBJECT = re.compile(r"^\s*EXCLUDE_OBJECT_DEFINE\s+NAME=(\S+)", re.IGNORECASE)
_M486 = re.compile(r"^\s*M486\s+S\s*(-?\d+)", re.IGNORECASE)
_E_MODE = re.compile(r"^\s*M8([23])\b", re.IGNORECASE)

CONFIG_DELIMITERS = (
    ("; prusaslicer_config = begin", "; prusaslicer_config = end"),
    ("; CONFIG_BLOCK_START", "; CONFIG_BLOCK_END"),
    ("; slic3r_config = begin", "; slic3r_config = end"),
    ("; SuperSlicer_config = begin", "; SuperSlicer_config = end"),
)


class GcodeError(Exception):
    """A G-code file could not be read or made sense of."""


@dataclass(slots=True)
class Move:
    """One G0/G1/G2/G3 line, decoded far enough to reason about Z and E."""

    cmd: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    e: float | None = None
    f: float | None = None


@dataclass
class Cursor:
    """Extruder and nozzle state while walking a run of lines."""

    relative_e: bool
    e_pos: float = 0.0
    x: float | None = None
    y: float | None = None
    retracted: float = 0.0  # millimetres of filament currently pulled back

    def copy(self) -> "Cursor":
        return replace(self)


@dataclass(slots=True)
class Step:
    """One decoded line: its E delta in millimetres and its XY travel, if any."""

    index: int
    line: str
    kind: str  # "move", "mode" or "other"
    delta: float | None
    dist: float


@dataclass(slots=True)
class Layer:
    """A printed layer: ``lines[start:end]`` of the owning file."""

    index: int  # 1-based, as printed
    z: float
    start: int
    end: int


@dataclass
class GcodeFile:
    path: Path
    lines: list[str]
    newline: str
    layers: list[Layer]
    header_end: int
    footer_start: int
    config: dict[str, str]
    config_span: tuple[int, int] | None
    layer_marker: str  # "layer_change", "cura" or "z_move"
    relative_e: bool
    object_instances: int | None = field(default=None)

    @property
    def header(self) -> list[str]:
        return self.lines[: self.header_end]

    @property
    def footer(self) -> list[str]:
        return self.lines[self.footer_start :]

    def layer_lines(self, layer: Layer) -> list[str]:
        return self.lines[layer.start : layer.end]

    def cursor(self) -> Cursor:
        return Cursor(relative_e=self.relative_e)


def strip_comment(line: str) -> tuple[str, str]:
    """Split a line into its code part and its trailing comment (including ``;``)."""
    idx = line.find(";")
    if idx < 0:
        return line, ""
    return line[:idx], line[idx:]


def parse_e_mode(line: str) -> bool | None:
    """True for M83 (relative E), False for M82, None if the line is neither."""
    match = _E_MODE.match(strip_comment(line)[0])
    return match.group(1) == "3" if match else None


def parse_move(line: str) -> Move | None:
    """Decode a linear or arc move. Returns None for anything else."""
    code, _ = strip_comment(line)
    stripped = code.lstrip()
    if not stripped or stripped[0] not in "Gg":
        return None
    words = _WORD.findall(stripped)
    if not words:
        return None
    letter, number = words[0]
    if letter not in "Gg":
        return None
    try:
        cmd = f"G{int(float(number))}"
    except ValueError:
        return None
    if cmd not in ("G0", "G1", "G2", "G3"):
        return None
    move = Move(cmd)
    for letter, number in words[1:]:
        key = letter.upper()
        if key not in ("X", "Y", "Z", "E", "F"):
            continue
        try:
            value = float(number)
        except ValueError:
            continue
        setattr(move, key.lower(), value)
    return move


def parse_g92_e(line: str) -> float | None:
    """Return the E value of a ``G92`` line, or None if it does not set E."""
    code, _ = strip_comment(line)
    if not re.match(r"^\s*G92\b", code, re.IGNORECASE):
        return None
    for letter, number in _WORD.findall(code):
        if letter.upper() == "E":
            try:
                return float(number)
            except ValueError:
                return None
    return None


def format_e(value: float) -> str:
    """Format an E value the way slicers do: up to 5 decimals, no trailing zeros."""
    text = f"{value:.5f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


def set_e(line: str, value: float) -> str:
    """Replace the E word of a move, leaving every other word and the comment alone."""
    code, comment = strip_comment(line)
    replaced = False

    def repl(match: re.Match[str]) -> str:
        nonlocal replaced
        if match.group(1).upper() != "E" or replaced:
            return match.group(0)
        replaced = True
        return f"{match.group(1)}{format_e(value)}"

    return _WORD.sub(repl, code) + comment


def walk(lines: list[str], cursor: Cursor):
    """Yield one Step per line, advancing ``cursor``.

    This is the single place that decides what a line means for the extruder, so
    the splice, the flow ramp and the checks all agree on it.
    """
    for index, line in enumerate(lines):
        mode = parse_e_mode(line)
        if mode is not None:
            cursor.relative_e = mode
            yield Step(index, line, "mode", None, 0.0)
            continue
        reset = parse_g92_e(line)
        if reset is not None:
            cursor.e_pos = reset
            yield Step(index, line, "other", None, 0.0)
            continue
        move = parse_move(line)
        if move is None:
            yield Step(index, line, "other", None, 0.0)
            continue

        dist = 0.0
        if (move.x is not None or move.y is not None) and None not in (cursor.x, cursor.y):
            dx = (move.x if move.x is not None else cursor.x) - cursor.x
            dy = (move.y if move.y is not None else cursor.y) - cursor.y
            dist = math.hypot(dx, dy)
        if move.x is not None:
            cursor.x = move.x
        if move.y is not None:
            cursor.y = move.y

        delta = None
        if move.e is not None:
            if cursor.relative_e:
                delta = move.e
                cursor.e_pos += move.e
            else:
                delta = round(move.e - cursor.e_pos, 5)
                cursor.e_pos = move.e
            # Negative E is always a retraction, even on a wiping move that also
            # travels; positive E only counts as priming when nothing else moves.
            if delta < 0 or dist == 0:
                cursor.retracted -= delta
        yield Step(index, line, "move", delta, dist)


def state_at(gcode: GcodeFile, line_index: int) -> Cursor:
    """Extruder and nozzle state the file has reached by ``line_index``.

    The layers taken from a file assume the nozzle is where that file left it, so
    the weld has to resume from this state rather than from nothing.
    """
    cursor = Cursor(relative_e=gcode.relative_e)
    for _ in walk(gcode.lines[:line_index], cursor):
        pass
    return cursor


def config_float(config: dict[str, str], key: str, fallback: float) -> float:
    """First numeric value of a config key, which slicers sometimes write as a list."""
    for piece in config.get(key, "").split(","):
        try:
            return float(piece.strip())
        except ValueError:
            continue
    return fallback


def bead_width(area: float, height: float) -> float:
    """Width of the bead an extrusion of cross-section ``area`` leaves.

    The usual model: a rectangle of length w-h capped with semicircles of radius
    h/2, so ``area = h*w - h**2 + pi*h**2/4``. Solved for w.
    """
    if height <= 0:
        return 0.0
    return (area + height * height * (1.0 - math.pi / 4.0)) / height


def read_text(path: Path) -> tuple[list[str], str]:
    """Read a G-code file, decoding binary G-code on the way in."""
    if path.is_dir():
        raise GcodeError(f"{path}: is a directory, not a G-code file")
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise GcodeError(f"{path}: no such file") from exc
    except PermissionError as exc:
        raise GcodeError(f"{path}: permission denied") from exc
    except OSError as exc:
        raise GcodeError(f"{path}: cannot read ({exc.strerror or exc})") from exc

    if not raw.strip():
        raise GcodeError(f"{path}: file is empty")

    if is_bgcode(raw):
        try:
            return decode(raw).splitlines(), "\n"
        except BgcodeError as exc:
            raise GcodeError(f"{path}: {exc}") from exc
    if path.suffix.lower() == ".bgcode":
        raise GcodeError(
            f"{path}: named .bgcode but does not start with the GCDE magic number. "
            "Re-export it from the slicer."
        )

    text = raw.decode("utf-8", errors="replace")
    newline = "\r\n" if text.count("\r\n") > text.count("\n") // 2 else "\n"
    return text.splitlines(), newline


def parse_config(lines: list[str]) -> tuple[dict[str, str], tuple[int, int] | None]:
    """Read the trailing slicer config comment block into a plain dict."""
    for begin_marker, end_marker in CONFIG_DELIMITERS:
        begin = end = None
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if end is None and line == end_marker:
                end = i
            elif end is not None and line == begin_marker:
                begin = i
                break
        if begin is None or end is None:
            continue
        config: dict[str, str] = {}
        for line in lines[begin + 1 : end]:
            match = _CONFIG_KV.match(line)
            if match:
                config[match.group(1)] = match.group(2)
        return config, (begin, end)
    return {}, None


def _detect_layer_starts(lines: list[str], scan: range) -> tuple[list[int], str]:
    starts = [i for i in scan if _LAYER_CHANGE.match(lines[i])]
    if starts:
        return starts, "layer_change"
    starts = [i for i in scan if _CURA_LAYER.match(lines[i])]
    if starts:
        return starts, "cura"

    starts = []
    current_z: float | None = None
    for i in scan:
        move = parse_move(lines[i])
        if move is None or move.z is None:
            continue
        if move.x is not None or move.y is not None or move.e is not None:
            continue
        if current_z is None or move.z > current_z + 1e-9:
            starts.append(i)
            current_z = move.z
    return starts, "z_move"


def _layer_z(lines: list[str], start: int, end: int) -> float | None:
    for i in range(start, min(start + 6, end)):
        match = _Z_MARKER.match(lines[i])
        if match:
            return float(match.group(1))
    for i in range(start, end):
        move = parse_move(lines[i])
        if move is not None and move.z is not None:
            return move.z
    return None


def _find_footer_start(lines: list[str], last_layer_start: int) -> int:
    last_extruding = last_layer_start
    for i in range(last_layer_start, len(lines)):
        move = parse_move(lines[i])
        if move is not None and move.e is not None:
            last_extruding = i
    custom = None
    for i in range(last_extruding + 1, len(lines)):
        if lines[i].strip().lower() == ";type:custom":
            custom = i
            break
    return custom if custom is not None else last_extruding + 1


def _detect_relative_e(lines: list[str], header_end: int, config: dict[str, str]) -> bool:
    for line in lines[:header_end]:
        mode = parse_e_mode(line)
        if mode is not None:
            return mode
    return config.get("use_relative_e_distances", "0").strip() in ("1", "true", "True")


def find_objects_info(lines: list[str], config: dict[str, str]) -> str | None:
    """PrusaSlicer writes ``; objects_info = {...}`` just outside the config block."""
    if config.get("objects_info"):
        return config["objects_info"]
    for line in reversed(lines):
        match = _CONFIG_KV.match(line)
        if match and match.group(1) == "objects_info":
            return match.group(2)
    return None


def _count_object_instances(lines: list[str], config: dict[str, str]) -> int | None:
    info = find_objects_info(lines, config)
    if info:
        count = info.count('"name"')
        if count:
            return count
    pairs = set()
    names = set()
    ids = set()
    for line in lines:
        match = _PRINTING_OBJECT.search(line)
        if match:
            pairs.add((match.group(1), match.group(2)))
            continue
        match = _EXCLUDE_OBJECT.match(line.strip())
        if match:
            names.add(match.group(1))
            continue
        match = _M486.match(line)
        if match and int(match.group(1)) >= 0:
            ids.add(match.group(1))
    for candidate in (pairs, names, ids):
        if candidate:
            return len(candidate)
    return None


def parse_file(path: str | Path) -> GcodeFile:
    """Parse a sliced G-code file. Raises GcodeError on anything unusable."""
    path = Path(path)
    lines, newline = read_text(path)
    config, config_span = parse_config(lines)
    # PrusaSlicer and OrcaSlicer append the config block; BambuStudio puts it near
    # the top. Either way it is not part of the print, so keep it out of the scan.
    scan = range(len(lines))
    if config_span:
        begin, end = config_span
        scan = range(end + 1, len(lines)) if begin < len(lines) // 2 else range(begin)

    starts, marker = _detect_layer_starts(lines, scan)
    if not starts:
        raise GcodeError(
            f"{path}: no layers found. Expected ';LAYER_CHANGE' markers or bare Z moves. "
            "Is this a sliced G-code file?"
        )

    footer_start = _find_footer_start(lines, starts[-1])
    bounds = starts + [footer_start]
    layers: list[Layer] = []
    for i, start in enumerate(starts):
        end = bounds[i + 1]
        z = _layer_z(lines, start, end)
        if z is None:
            continue
        # Spiral vase emits its flow ramp-down as a second block at the final Z;
        # that is a continuation of the layer, not a new one.
        if layers and z <= layers[-1].z + 1e-9:
            layers[-1].end = end
            continue
        layers.append(Layer(index=len(layers) + 1, z=z, start=start, end=end))
    if len(layers) < 2:
        raise GcodeError(f"{path}: only {len(layers)} layer(s) found; nothing to weld")

    header_end = layers[0].start
    return GcodeFile(
        path=path,
        lines=lines,
        newline=newline,
        layers=layers,
        header_end=header_end,
        footer_start=footer_start,
        config=config,
        config_span=config_span,
        layer_marker=marker,
        relative_e=_detect_relative_e(lines, header_end, config),
        object_instances=_count_object_instances(lines, config),
    )
