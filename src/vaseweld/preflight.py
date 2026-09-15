"""Refuse a plate `auto` cannot weld before spending two slices on it.

`compat.check_compatible` catches the same plates after slicing, from the G-code.
This looks at the project instead, so a two-object plate costs a message rather
than two full slicing runs.

It is a fast fail, not a second validator: a plate that gets past here can still
be refused later. Regions created by modifier volumes or per-volume print
settings are not modelled, only object count, instance count and per-volume
extruder assignment.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .compat import SINGLE_MATERIAL_MSG, SINGLE_OBJECT_MSG

MODEL_CONFIG = "Metadata/Slic3r_PE_model.config"
MODEL_FILE = "3D/3dmodel.model"

_OBJECT = re.compile(r'<object\b[^>]*\binstances_count="(\d+)"')
_BUILD_ITEM = re.compile(r'<item\b[^>]*?\bobjectid="(\d+)"[^>]*>')
_UNPRINTABLE = re.compile(r'\bprintable="0"')
_EXTRUDER = re.compile(
    r'<metadata\b[^>]*\btype="(object|volume)"[^>]*\bkey="extruder"[^>]*\bvalue="(\d+)"'
)


class PreflightError(Exception):
    """The project cannot be welded. The message says which rule it broke."""


@dataclass(frozen=True)
class Plate:
    """What the project says about the plate, before anything is sliced."""

    path: Path
    objects: int
    instances: int
    extruders: frozenset[int]
    inspected: bool

    def describe(self) -> str:
        if not self.inspected:
            return f"{self.path.name}: single mesh"
        noun = "object" if self.objects == 1 else "objects"
        copies = "" if self.instances == self.objects else f", {self.instances} instances"
        return f"{self.path.name}: {self.objects} {noun}{copies}"


def inspect_plate(path: Path) -> Plate:
    """Read object and material counts out of a 3MF. Other formats are single meshes."""
    if not path.exists():
        raise PreflightError(f"{path}: no such file")
    if path.is_dir():
        raise PreflightError(f"{path} is a directory, not a model")
    if path.suffix.lower() != ".3mf":
        return Plate(path=path, objects=1, instances=1, extruders=frozenset(), inspected=False)

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            config = _read_member(archive, MODEL_CONFIG, names)
            model = _read_member(archive, MODEL_FILE, names)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
        raise PreflightError(
            f"{path.name}: not a readable 3MF ({exc}). "
            "Re-save the project from PrusaSlicer, or pass the model file instead."
        ) from exc

    counts = [int(n) for n in _OBJECT.findall(config)]
    live = _printable_items(model)
    if live is None:
        objects = len(counts) or 1
        instances = sum(counts) or 1
    else:
        instances = len(live)
        # the config groups copies under one <object>, the build lists them one per <item>,
        # so only the config can tell "two objects" from "two copies of one"
        objects = 1 if len(counts) == 1 and instances > 1 else len(set(live)) or len(counts) or 1
    return Plate(
        path=path,
        objects=objects,
        instances=instances,
        extruders=_extruders_used(config),
        inspected=True,
    )


def _extruders_used(config: str) -> frozenset[int]:
    """The extruders the volumes actually print with, resolved through the object default."""
    assigned = [(scope, int(n)) for scope, n in _EXTRUDER.findall(config)]
    # 0 means "inherit", so a volume at 0 beside a volume at 2 is still two materials
    default = next((n for scope, n in assigned if scope == "object" and n), 1)
    return frozenset(n or default for scope, n in assigned if scope == "volume")


def _printable_items(model: str) -> list[str] | None:
    """Object ids of the instances actually set to print, or None if there is no build section."""
    items = [(m.group(1), m.group(0)) for m in _BUILD_ITEM.finditer(model)]
    if not items:
        return None
    return [objectid for objectid, tag in items if not _UNPRINTABLE.search(tag)]


def _read_member(archive: zipfile.ZipFile, name: str, names: set[str]) -> str:
    if name not in names:
        return ""
    return archive.read(name).decode("utf-8", errors="replace")


def check_plate(path: Path) -> Plate:
    """Raise PreflightError unless this project is a single-material single object."""
    plate = inspect_plate(path)
    if plate.instances < 1:
        raise PreflightError(
            f"{plate.path.name} has nothing on it set to print. "
            "Set the object printable in PrusaSlicer and save the project again."
        )
    if plate.instances > 1 or plate.objects > 1:
        what = (
            f"{plate.objects} objects"
            if plate.objects > 1
            else f"{plate.instances} copies of one object"
        )
        raise PreflightError(
            f"{plate.path.name} is a plate with {what}. {SINGLE_OBJECT_MSG} "
            "Slice the plate you actually want to weld, with one object on it."
        )
    if len(plate.extruders) > 1:
        used = ", ".join(str(n) for n in sorted(plate.extruders))
        raise PreflightError(
            f"{plate.path.name} assigns its parts to extruders {used}. {SINGLE_MATERIAL_MSG}"
        )
    return plate
