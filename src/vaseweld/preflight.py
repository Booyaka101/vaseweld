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
_BUILD_ITEM = re.compile(r"<item\b[^>]*objectid=\"(\d+)\"")
_EXTRUDER = re.compile(r'<metadata\b[^>]*\bkey="extruder"[^>]*\bvalue="(\d+)"')


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
    items = _BUILD_ITEM.findall(model)
    objects = len(counts) or len(set(items)) or 1
    instances = sum(counts) if counts else (len(items) or 1)
    extruders = frozenset(int(n) for n in _EXTRUDER.findall(config))
    return Plate(
        path=path,
        objects=objects,
        instances=instances,
        extruders=extruders,
        inspected=True,
    )


def _read_member(archive: zipfile.ZipFile, name: str, names: set[str]) -> str:
    if name not in names:
        return ""
    return archive.read(name).decode("utf-8", errors="replace")


def check_plate(path: Path) -> Plate:
    """Raise PreflightError unless this project is a single-material single object."""
    plate = inspect_plate(path)
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
