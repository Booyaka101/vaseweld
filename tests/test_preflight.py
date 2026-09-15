from __future__ import annotations

import zipfile

import pytest

from conftest import example, fixture, multi_material_3mf
from vaseweld.preflight import MODEL_CONFIG, PreflightError, check_plate, inspect_plate


def test_a_single_object_project_is_accepted():
    plate = check_plate(fixture("cylinder_6mm.3mf"))
    assert (plate.objects, plate.instances) == (1, 1)
    assert plate.describe() == "cylinder_6mm.3mf: 1 object"


def test_two_copies_of_one_object_are_refused():
    with pytest.raises(PreflightError) as excinfo:
        check_plate(fixture("two_objects.3mf"))
    message = str(excinfo.value)
    assert "2 copies of one object" in message
    assert "single object" in message


def test_two_extruders_are_refused(tmp_path):
    with pytest.raises(PreflightError) as excinfo:
        check_plate(multi_material_3mf(tmp_path))
    assert "extruders 1, 2" in str(excinfo.value)


def test_a_bare_model_file_is_taken_on_trust():
    plate = check_plate(example("cylinder_6mm.stl"))
    assert not plate.inspected
    assert plate.describe() == "cylinder_6mm.stl: single mesh"


def test_a_missing_project_is_named():
    with pytest.raises(PreflightError, match="no such file"):
        check_plate(fixture("cylinder_6mm.3mf").with_name("nope.3mf"))


def test_a_3mf_that_is_not_a_zip_says_so(tmp_path):
    broken = tmp_path / "broken.3mf"
    broken.write_text("this is not a zip", encoding="utf-8")
    with pytest.raises(PreflightError, match="not a readable 3MF"):
        check_plate(broken)


def test_a_3mf_without_the_slic3r_config_falls_back_to_the_build_items(tmp_path):
    """Projects saved by another tool have 3D/3dmodel.model and nothing else we read."""
    source = fixture("two_objects.3mf")
    stripped = tmp_path / "stripped.3mf"
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(stripped, "w") as out:
        for item in archive.infolist():
            if item.filename != MODEL_CONFIG:
                out.writestr(item, archive.read(item.filename))
    plate = inspect_plate(stripped)
    assert plate.instances == 2
    with pytest.raises(PreflightError, match="2 objects"):
        check_plate(stripped)
