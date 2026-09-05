"""The shape a slicer post-processing hook invokes: one file appended as the last argument."""

from __future__ import annotations

import shutil

import pytest

from conftest import fixture, weld_argv
from vaseweld.cli import EXIT_OK, EXIT_USAGE, main


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err.splitlines()


@pytest.fixture
def temp_slice(tmp_path):
    def _copy(name):
        target = tmp_path / "temp.gcode"
        shutil.copy(fixture(name), target)
        return target

    return _copy


@pytest.mark.parametrize(
    "missing, copied, kept, expected_line",
    [
        (
            "vase",
            "prusaslicer_vase_40mm.gcode",
            "prusaslicer_normal_40mm.gcode",
            "normal: layers 1-61 (Z 0.200-12.200)",
        ),
        (
            "normal",
            "prusaslicer_normal_40mm.gcode",
            "prusaslicer_vase_40mm.gcode",
            "vase: layers 62-200 (Z 12.400-40.000)",
        ),
    ],
)
def test_trailing_file_fills_the_missing_side(
    temp_slice, capsys, missing, copied, kept, expected_line
):
    temp = temp_slice(copied)
    before = temp.stat().st_size
    other = "normal" if missing == "vase" else "vase"
    code, stdout, _ = run(
        weld_argv(**{missing: None, other: kept}, at="12.4", trailing=temp), capsys
    )
    assert code == EXIT_OK
    assert expected_line in stdout
    assert stdout[-1] == (
        f"wrote {temp} ({len(temp.read_text(encoding='utf-8').splitlines())} lines)"
    )
    assert temp.stat().st_size != before


def test_slic3r_pp_output_name_is_reported(temp_slice, capsys, monkeypatch):
    monkeypatch.setenv("SLIC3R_PP_OUTPUT_NAME", r"C:\prints\hybrid.gcode")
    temp = temp_slice("prusaslicer_vase_6mm.gcode")
    code, stdout, _ = run(weld_argv(vase=None, trailing=temp), capsys)
    assert code == EXIT_OK
    assert stdout[-1] == r"slicer will save it as C:\prints\hybrid.gcode"


def test_both_sides_plus_a_trailing_file_is_refused(temp_slice, capsys):
    temp = temp_slice("prusaslicer_vase_6mm.gcode")
    code, _, stderr = run(weld_argv(trailing=temp), capsys)
    assert code == EXIT_USAGE
    assert "has no role" in stderr[0]
