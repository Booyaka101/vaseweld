from __future__ import annotations

import pytest

from conftest import fixture
from vaseweld.parser import (
    GcodeError,
    format_e,
    parse_e_mode,
    parse_file,
    parse_g92_e,
    parse_move,
    set_e,
)


def test_prusaslicer_layers(ps_normal):
    assert len(ps_normal.layers) == 200
    assert ps_normal.layers[0].z == pytest.approx(0.2)
    assert ps_normal.layers[61].z == pytest.approx(12.4)
    assert ps_normal.layers[-1].z == pytest.approx(40.0)
    assert ps_normal.layer_marker == "layer_change"
    assert ps_normal.relative_e is False


def test_orcaslicer_layers_and_mode(orca_normal, orca_vase):
    assert len(orca_normal.layers) == 200
    assert orca_normal.relative_e is True
    # The spiral vase ramp-down is a second block at the final Z, not a 201st layer.
    assert len(orca_vase.layers) == 200
    assert orca_vase.layers[-1].z == pytest.approx(40.0)


def test_config_block_parsed_for_both_slicers(ps_normal, orca_normal):
    assert ps_normal.config["layer_height"] == "0.2"
    assert ps_normal.config["use_relative_e_distances"] == "0"
    assert orca_normal.config["layer_height"] == "0.2"
    assert orca_normal.config["printer_model"] == "Creality Ender-3"


def test_header_and_footer_bracket_the_layers(ps_normal):
    assert ps_normal.header_end == ps_normal.layers[0].start
    assert ps_normal.footer_start == ps_normal.layers[-1].end
    assert any("prusaslicer_config = begin" in line for line in ps_normal.footer)
    assert any("M109" in line for line in ps_normal.header)


def test_object_instances_counted(orca_normal):
    assert orca_normal.object_instances == 1
    assert parse_file(fixture("two_objects_1mm.gcode")).object_instances == 2


def test_binary_gcode_is_refused_with_a_fix():
    with pytest.raises(GcodeError, match="binary G-code"):
        parse_file(fixture("binary_6mm.bgcode"))


def test_missing_file():
    with pytest.raises(GcodeError, match="no such file"):
        parse_file(fixture("prusaslicer_normal_6mm.gcode").with_name("nope.gcode"))


def test_empty_file(tmp_path):
    empty = tmp_path / "empty.gcode"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(GcodeError, match="empty"):
        parse_file(empty)


def test_file_without_layers(tmp_path):
    path = tmp_path / "notgcode.gcode"
    path.write_text("hello\nworld\n", encoding="utf-8")
    with pytest.raises(GcodeError, match="no layers found"):
        parse_file(path)


def test_directory_instead_of_file(tmp_path):
    with pytest.raises(GcodeError, match="is a directory"):
        parse_file(tmp_path)


@pytest.mark.parametrize(
    "line, expected",
    [
        ("G1 X10 Y20 E0.5 F1800", ("G1", 10.0, 20.0, None, 0.5)),
        ("G0 Z1.5", ("G0", None, None, 1.5, None)),
        ("G2 X1 Y2 I3 J4 E0.1", ("G2", 1.0, 2.0, None, 0.1)),
        ("G1 E-2 F2400 ; retract", ("G1", None, None, None, -2.0)),
    ],
)
def test_parse_move(line, expected):
    move = parse_move(line)
    assert (move.cmd, move.x, move.y, move.z, move.e) == expected


@pytest.mark.parametrize("line", ["M104 S200", "; G1 X1 E1", "", "G28 ; home"])
def test_parse_move_ignores_non_moves(line):
    assert parse_move(line) is None


def test_parse_g92_and_mode():
    assert parse_g92_e("G92 E0") == 0.0
    assert parse_g92_e("G92 X0 Y0") is None
    assert parse_e_mode("M83 ; relative") is True
    assert parse_e_mode("M82") is False
    assert parse_e_mode("M84") is None


def test_set_e_leaves_the_rest_of_the_line_alone():
    assert set_e("G1 X1.5 Y2 E12.5 F1800 ; wall", 0.25) == "G1 X1.5 Y2 E0.25 F1800 ; wall"
    assert set_e("G2 X1 Y2 I3 J4 E1.0", -2.0) == "G2 X1 Y2 I3 J4 E-2"


def test_format_e_matches_slicer_style():
    assert format_e(2.047131) == "2.04713"
    assert format_e(0.0) == "0"
    assert format_e(-2.0) == "-2"
