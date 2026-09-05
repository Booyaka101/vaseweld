from __future__ import annotations

from pathlib import Path

import pytest

from vaseweld.parser import parse_file

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> Path:
    path = FIXTURES / name
    assert path.exists(), f"missing fixture {name}"
    return path


@pytest.fixture(scope="session")
def ps_normal():
    return parse_file(fixture("prusaslicer_normal_40mm.gcode"))


@pytest.fixture(scope="session")
def ps_vase():
    return parse_file(fixture("prusaslicer_vase_40mm.gcode"))


@pytest.fixture(scope="session")
def orca_normal():
    return parse_file(fixture("orcaslicer_normal_40mm.gcode"))


@pytest.fixture(scope="session")
def orca_vase():
    return parse_file(fixture("orcaslicer_vase_40mm.gcode"))


@pytest.fixture(scope="session")
def klipper_normal():
    return parse_file(fixture("klipper_normal_6mm.gcode"))


@pytest.fixture(scope="session")
def klipper_vase():
    return parse_file(fixture("klipper_vase_6mm.gcode"))


@pytest.fixture(scope="session")
def small_normal():
    return parse_file(fixture("prusaslicer_normal_6mm.gcode"))


@pytest.fixture(scope="session")
def small_vase():
    return parse_file(fixture("prusaslicer_vase_6mm.gcode"))


def weld_argv(
    normal="prusaslicer_normal_6mm.gcode",
    vase="prusaslicer_vase_6mm.gcode",
    at="3",
    output=None,
    extra=(),
    trailing=None,
):
    """Build a `weld` argv. A None name omits that flag; a Path is used verbatim."""
    argv = ["weld"]
    for flag, value in (("--normal", normal), ("--vase", vase)):
        if value is not None:
            argv += [flag, str(value if isinstance(value, Path) else fixture(value))]
    argv += ["--at", at, *extra]
    if output is not None:
        argv += ["-o", str(output)]
    if trailing is not None:
        argv.append(str(trailing))
    return argv


@pytest.fixture(scope="session")
def bambu_normal():
    return parse_file(fixture("bambustudio_normal_40mm.gcode"))


@pytest.fixture(scope="session")
def bambu_vase():
    return parse_file(fixture("bambustudio_vase_40mm.gcode"))
