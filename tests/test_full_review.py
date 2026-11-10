"""Regressions from the correctness, safety and usability review."""

import json
from dataclasses import replace

import pytest
from conftest import fixture

from vaseweld.bgcode import BgcodeError, meatpack
from vaseweld.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, main
from vaseweld.parser import Cursor, parse_file, parse_move
from vaseweld.validate import check
from vaseweld.weld import WeldError, _Opening, _seam_lines, weld


def sliced(tmp_path, header, body):
    path = tmp_path / "input.gcode"
    path.write_text("\n".join([
        *header, ";LAYER_CHANGE", ";Z:0.2", "G1 X10 Y0 Z0.2 E101",
        ";LAYER_CHANGE", ";Z:0.4", "G1 Z0.4", *body, "M104 S0",
    ]) + "\n", encoding="utf-8")
    return path


def test_validator_uses_absolute_extrusion_position_from_header(tmp_path):
    path = sliced(tmp_path, ["M82", "G92 E100", "G1 X0 Y0"],
                  ["G1 E100.2", "G1 E101", "G1 X20 Y0 E102"])
    assert check(path).ok


def test_validator_uses_header_position_for_first_printing_move(tmp_path):
    path = sliced(tmp_path, ["M83", "G1 X0 Y0"], ["G1 X20 Y0 E1"])
    report = check(path)
    assert not any(problem.check == "e" for problem in report.problems)


def test_validator_retains_header_retraction_but_not_startup_priming(tmp_path):
    path = sliced(tmp_path, ["M82", "G1 E100", "G1 E99.2", "G1 X0 Y0"],
                  ["G1 X20 Y0 E102"])
    assert check(path).ok


@pytest.mark.parametrize("wait", ["M109S215", "m190s60", "M109 S+215", "M190R60"])
def test_compact_and_lowercase_temperature_waits_are_detected(tmp_path, wait):
    path = sliced(tmp_path, ["M82", "G92 E100", "G1 X0 Y0"],
                  [wait, "G1 X20 Y0 E102"])
    report = check(path)
    assert [problem.check for problem in report.problems] == ["temperature"]
    assert report.temperature_timelines == 2


@pytest.mark.parametrize("retract", [False, True])
def test_seam_matches_a_more_retracted_resume_state(retract):
    seam = Cursor(relative_e=True, x=10, y=10, retracted=0)
    resume = Cursor(relative_e=True, x=10, y=10, retracted=0.8)
    result = _seam_lines({}, seam, resume, _Opening(printing_at=1), retract=retract)
    assert parse_move(result.at_boundary[0]).e == pytest.approx(-0.8)
    assert sum(parse_move(line).e or 0 for line in result.before_printing) == 0


def test_weld_matches_retraction_before_the_incoming_layer_primes(tmp_path):
    def source(name, retract):
        path = tmp_path / name
        lines = ["M83", "G1 X0 Y0", ";LAYER_CHANGE", ";Z:0.2", "G1 Z0.2",
                 "G1 X10 Y0 E1", *(["G1 E-0.8"] if retract else []),
                 ";LAYER_CHANGE", ";Z:0.4", "G1 Z0.4",
                 *(["G1 E0.8"] if retract else []), "G1 X20 Y0 E1", "M104 S0"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return parse_file(path)

    result = weld(source("normal.gcode", False), source("vase.gcode", True), 0.4,
                  start_flow=1, seam_retract=False)
    output = tmp_path / "welded.gcode"
    output.write_text("\n".join(result.lines) + "\n", encoding="utf-8")
    assert check(output).ok
    assert sum(move.e or 0 for line in result.lines
               if (move := parse_move(line)) is not None) == pytest.approx(2)


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
@pytest.mark.parametrize("key", ["spiral_starting_flow_ratio", "spiral_finishing_flow_ratio"])
def test_invalid_profile_flow_is_refused(small_normal, small_vase, key, value):
    vase = replace(small_vase, config={**small_vase.config, key: str(value)})
    with pytest.raises(WeldError, match=key):
        weld(small_normal, vase, 3)


@pytest.mark.parametrize("option", ["start_flow", "finish_flow"])
def test_library_flow_overrides_are_validated(small_normal, small_vase, option):
    with pytest.raises(WeldError, match="flow"):
        weld(small_normal, small_vase, 3, **{option: float("nan")})


def test_library_rejects_unknown_first_role(small_normal, small_vase):
    with pytest.raises(WeldError, match="first_role"):
        weld(small_normal, small_vase, 3, first_role="typo")


@pytest.mark.parametrize("tail", [b"\xf1", b"\xff", b"\xff\xff"])
def test_truncated_meatpack_is_refused(tail):
    with pytest.raises(BgcodeError, match="truncated"):
        meatpack(b"\xff\xff\xfb" + tail, False)


def test_meatpack_reset_clears_no_spaces_mode():
    commands = b"\xff\xff\xf7\xff\xff\xf9\xff\xff\xfb"
    assert meatpack(commands + b"\x1b", False) == " 1"


@pytest.mark.parametrize("source, expected", [
    ("prusaslicer_normal_6mm.gcode", EXIT_OK),
    ("two_objects_1mm.gcode", EXIT_OK),
])
def test_check_json_preserves_exit_codes_and_exposes_structured_results(source, expected, capsys):
    assert main(["check", str(fixture(source)), "--json"]) == expected
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["path"] == str(fixture(source))
    assert report["problems"] == []
    assert isinstance(report["relative_e"], bool)


def test_check_json_reports_problem_lines(tmp_path, capsys):
    path = sliced(tmp_path, ["M82", "G92 E100", "G1 X0 Y0"],
                  ["M109 S215", "G1 X20 Y0 E102"])
    assert main(["check", str(path), "--json"]) == EXIT_PROBLEMS
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["problems"][0]["check"] == "temperature"
    assert report["problems"][0]["line"] == 10


def test_check_json_unreadable_input_keeps_usage_exit_code(tmp_path, capsys):
    assert main(["check", str(tmp_path / "missing.gcode"), "--json"]) == EXIT_USAGE
    captured = capsys.readouterr()
    assert not captured.out
    assert "no such file" in captured.err


@pytest.mark.parametrize("command", ["G1.1 X10 E1", "G0.1 X10 E1", "G2.1 X10 E1"])
def test_decimal_gcodes_are_not_truncated_to_motion_commands(command):
    assert parse_move(command) is None


def test_header_extrusion_mode_uses_the_last_mode(tmp_path):
    path = sliced(tmp_path, ["M82", "M83", "G1 X0 Y0"], ["G1 X20 Y0 E1"])
    assert parse_file(path).relative_e is True
