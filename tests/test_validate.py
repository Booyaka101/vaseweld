"""The four checks, each proved against a deliberately corrupted real weld."""

from __future__ import annotations

import re

import pytest

from conftest import fixture
from vaseweld.parser import GcodeError, parse_move
from vaseweld.validate import check
from vaseweld.weld import weld


@pytest.fixture(scope="module")
def welded_lines(ps_normal, ps_vase):
    return weld(ps_normal, ps_vase, 12.4).lines


@pytest.fixture
def write(tmp_path):
    def _write(lines, name="out.gcode"):
        path = tmp_path / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    return _write


def find(lines, predicate, start=0):
    return next(i for i in range(start, len(lines)) if predicate(lines[i]))


def test_a_clean_weld_passes(welded_lines, write):
    report = check(write(welded_lines))
    assert report.ok
    assert report.summary() == (
        "OK: Z monotonic, E coherent (relative), retractions balanced, 1 temperature timeline"
    )


@pytest.mark.parametrize(
    "name",
    [
        "prusaslicer_normal_40mm.gcode",
        "orcaslicer_vase_40mm.gcode",
        "klipper_normal_6mm.gcode",
        "arcfit_normal_6mm.gcode",
    ],
)
def test_untouched_slicer_output_passes(name):
    assert check(fixture(name)).ok


def test_non_monotonic_z_is_caught(welded_lines, write):
    lines = list(welded_lines)
    index = find(
        lines,
        lambda line: (move := parse_move(line)) and move.z is not None and move.e and move.z > 20,
    )
    lines[index] = re.sub(r"Z[\d.]+", "Z12.0", lines[index])
    report = check(write(lines))
    assert not report.ok
    assert [problem.check for problem in report.problems] == ["z"]
    assert "Z goes backwards" in report.problems[0].message


def test_unbalanced_retraction_is_caught(welded_lines, write):
    lines = list(welded_lines)
    index = find(
        lines,
        lambda line: (
            (move := parse_move(line))
            and move.e
            and move.e > 0
            and move.x is None
            and move.y is None
            and move.z is None
        ),
        start=200,
    )
    del lines[index]
    report = check(write(lines))
    assert not report.ok
    assert [problem.check for problem in report.problems] == ["retract"]
    assert "do not balance" in report.problems[0].message


def test_orphaned_m109_is_caught(welded_lines, write):
    lines = list(welded_lines)
    lines.insert(len(lines) // 2, "M109 S215 ; set temperature and wait")
    report = check(write(lines))
    assert not report.ok
    assert [problem.check for problem in report.problems] == ["temperature"]
    assert report.temperature_timelines == 2
    assert "M109 S215" in report.problems[0].message


def test_e_discontinuity_is_caught(welded_lines, write):
    lines = list(welded_lines)
    index = find(
        lines,
        lambda line: (move := parse_move(line)) and move.e and move.e > 0 and move.x is not None,
        start=3000,
    )
    lines[index] = re.sub(r"E[\d.]+", "E742.19", lines[index])
    report = check(write(lines))
    assert not report.ok
    assert [problem.check for problem in report.problems] == ["e"]
    assert "absolute E value" in report.problems[0].message


def test_mid_print_mode_switch_is_caught(welded_lines, write):
    lines = list(welded_lines)
    lines.insert(len(lines) // 2, "M82 ; back to absolute")
    report = check(write(lines))
    assert not report.ok
    assert any("switches to absolute" in problem.message for problem in report.problems)


def test_check_refuses_binary_gcode():
    with pytest.raises(GcodeError, match="binary G-code"):
        check(fixture("binary_6mm.bgcode"))
