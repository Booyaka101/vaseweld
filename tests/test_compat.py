from __future__ import annotations

import pytest

from conftest import fixture
from vaseweld.compat import CompatError, check_compatible, first_layer_footprint, normalise
from vaseweld.parser import parse_file


def test_matching_pair_is_accepted(ps_normal, ps_vase, orca_normal, orca_vase):
    check_compatible(ps_normal, ps_vase)
    check_compatible(orca_normal, orca_vase)


def test_layer_height_mismatch_names_the_field(small_normal):
    other = parse_file(fixture("mismatch_layerheight_6mm.gcode"))
    with pytest.raises(CompatError) as excinfo:
        check_compatible(small_normal, other)
    assert "'layer_height'" in str(excinfo.value)
    assert "0.3" in str(excinfo.value)


def test_bed_shape_mismatch_names_the_field(ps_normal, orca_vase):
    with pytest.raises(CompatError) as excinfo:
        check_compatible(ps_normal, orca_vase)
    assert "'bed_shape'" in str(excinfo.value)


def test_moved_object_is_refused(small_normal):
    shifted = parse_file(fixture("shifted_placement_1mm.gcode"))
    with pytest.raises(CompatError) as excinfo:
        check_compatible(small_normal, shifted)
    assert "'object placement'" in str(excinfo.value)


def test_two_objects_quote_prusaslicer(small_normal):
    two = parse_file(fixture("two_objects_1mm.gcode"))
    with pytest.raises(CompatError) as excinfo:
        check_compatible(small_normal, two)
    assert "Only a single object may be printed at a time in Spiral Vase mode." in str(
        excinfo.value
    )


def test_second_extruder_quotes_prusaslicer(small_normal, small_vase, tmp_path):
    lines = list(small_normal.lines)
    lines.insert(small_normal.layers[3].start, "T1")
    lines.insert(small_normal.layers[2].start, "T0")
    path = tmp_path / "twotool.gcode"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CompatError) as excinfo:
        check_compatible(parse_file(path), small_vase)
    assert "The Spiral Vase option can only be used when printing single material objects." in str(
        excinfo.value
    )


def test_normalise_collapses_slicer_number_formatting():
    assert normalise("first_layer_height", "0.200") == normalise("first_layer_height", "0.2")
    assert normalise("nozzle_diameter", "0.40, 0.40") == "0.4,0.4"
    assert normalise("bed_shape", "0x0,220x0") == normalise("bed_shape", "0.000x0,220.0x0.0")


def test_footprint_is_the_object_not_the_skirt(ps_normal, ps_vase):
    normal, vase = first_layer_footprint(ps_normal), first_layer_footprint(ps_vase)
    assert normal is not None and vase is not None
    assert not normal.differs_from(vase, 0.05)
    # The object is a 20 mm cylinder centred on the 200 mm bed.
    assert normal.min_x == pytest.approx(90.0, abs=0.5)
    assert normal.max_x == pytest.approx(110.0, abs=0.5)
