from __future__ import annotations

import re
import shutil
import tracemalloc
import zipfile

import pytest

from conftest import _replace_member, example, fixture, multi_material_3mf
from vaseweld.preflight import (
    MODEL_CONFIG,
    MODEL_FILE,
    PreflightError,
    check_plate,
    inspect_plate,
)

ITEM = '  <item objectid="1" transform="1 0 0 0 1 0 0 0 1 125 105 0" printable="1"/>\n'
SECOND = '  <item objectid="2" transform="1 0 0 0 1 0 0 0 1 90 105 0" printable="1"/>\n'
PARKED = '  <item objectid="9" transform="1 0 0 0 1 0 0 0 1 90 105 0" printable="0"/>\n'


def _with_build(tmp_path, name, items):
    """cylinder_6mm.3mf rebuilt with these build items, one <object> in the config per item."""
    source, destination = fixture("cylinder_6mm.3mf"), tmp_path / name
    shutil.copyfile(source, destination)

    model = zipfile.ZipFile(source).read(MODEL_FILE).decode()
    model = re.sub(r"<build>.*</build>", f"<build>\n{items} </build>", model, flags=re.S)
    _replace_member(destination, MODEL_FILE, model)

    config = zipfile.ZipFile(source).read(MODEL_CONFIG).decode()
    head, body = config.split(" <object", 1)
    body = " <object" + body[: body.index("</config>")]
    ids = re.findall(r'objectid="(\d+)"', items)
    _replace_member(
        destination,
        MODEL_CONFIG,
        head + "".join(body.replace('id="1"', f'id="{n}"', 1) for n in ids) + "</config>\n",
    )
    return destination


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


def test_the_default_extruder_is_not_a_second_material(tmp_path):
    """PrusaSlicer writes extruder 0 for "inherit the default", not for a second tool."""
    source, destination = fixture("cylinder_6mm.3mf"), tmp_path / "default_extruder.3mf"
    config = zipfile.ZipFile(source).read(MODEL_CONFIG).decode()
    config = config.replace(
        "  </volume>", '   <metadata type="volume" key="extruder" value="0"/>\n  </volume>'
    ).replace("  <volume", '  <metadata type="object" key="extruder" value="1"/>\n  <volume')
    shutil.copyfile(source, destination)
    _replace_member(destination, MODEL_CONFIG, config)
    assert check_plate(destination).extruders == frozenset({1})


def test_an_object_parked_as_not_printable_is_not_on_the_plate(tmp_path):
    plate = check_plate(_with_build(tmp_path, "parked.3mf", ITEM + PARKED))
    assert (plate.objects, plate.instances) == (1, 1)


# an empty <build> is an empty plate, and the config still lists the objects that used to be on it
@pytest.mark.parametrize("items", [PARKED, ""], ids=["parked", "no-items"])
def test_a_plate_with_nothing_printable_says_so(tmp_path, items):
    with pytest.raises(PreflightError) as excinfo:
        check_plate(_with_build(tmp_path, "empty.3mf", items))
    assert "nothing on it set to print" in str(excinfo.value)


def test_two_printable_objects_are_still_two_objects(tmp_path):
    with pytest.raises(PreflightError) as excinfo:
        check_plate(_with_build(tmp_path, "pair.3mf", ITEM + SECOND))
    assert "2 objects" in str(excinfo.value)


def test_two_objects_with_two_copies_each_are_two_objects_and_four_instances(tmp_path):
    """A copy is its own <object> in the model, aliased to the first, but one line in the config."""
    source, destination = fixture("two_objects.3mf"), tmp_path / "pairs.3mf"
    shutil.copyfile(source, destination)

    alias = '  <object id="%d" type="model">\n   <components>\n    <component objectid="1"/>\n'
    alias += "   </components>\n  </object>\n"
    model = zipfile.ZipFile(source).read(MODEL_FILE).decode()
    model = model.replace(" </resources>", (alias % 3) + (alias % 4) + " </resources>")
    item = '  <item objectid="%d" transform="1 0 0 0 1 0 0 0 1 %d 105 0" printable="1"/>\n'
    model = model.replace(" </build>", (item % (3, 90)) + (item % (4, 70)) + " </build>")
    _replace_member(destination, MODEL_FILE, model)

    config = zipfile.ZipFile(source).read(MODEL_CONFIG).decode()
    head, body = config.split(" <object", 1)
    block = " <object" + body[: body.index("</config>")]
    _replace_member(
        destination,
        MODEL_CONFIG,
        head + block + block.replace('id="1"', 'id="3"', 1) + "</config>\n",
    )

    plate = inspect_plate(destination)
    assert (plate.objects, plate.instances) == (2, 4)
    assert plate.describe() == "pairs.3mf: 2 objects, 4 instances"
    with pytest.raises(PreflightError, match="2 objects"):
        check_plate(destination)


def test_a_support_enforcer_is_not_a_second_material(tmp_path):
    """It carries no extruder key and lays no plastic, so inheriting the object's is not a tool."""
    source, destination = fixture("cylinder_6mm.3mf"), tmp_path / "enforced.3mf"
    config = zipfile.ZipFile(source).read(MODEL_CONFIG).decode()
    enforcer = (
        '  <volume firstid="192" lastid="203">\n'
        '   <metadata type="volume" key="volume_type" value="SupportEnforcer"/>\n'
        "  </volume>\n"
    )
    config = config.replace(
        "  </volume>", '   <metadata type="volume" key="extruder" value="2"/>\n  </volume>'
    ).replace(" </object>", enforcer + " </object>")
    shutil.copyfile(source, destination)
    _replace_member(destination, MODEL_CONFIG, config)
    assert check_plate(destination).extruders == frozenset({2})


def test_a_volume_left_at_the_default_beside_a_second_extruder_is_two_materials(tmp_path):
    """extruder="0" means "whatever the object uses", so 0 and 2 are extruders 1 and 2."""
    project = multi_material_3mf(tmp_path, extruders=(0, 2))
    assert inspect_plate(project).extruders == frozenset({1, 2})
    with pytest.raises(PreflightError, match="extruders 1, 2"):
        check_plate(project)


def test_a_volume_with_no_extruder_key_at_all_still_counts(tmp_path):
    """PrusaSlicer writes the key only for a volume someone assigned. The rest inherit."""
    project = multi_material_3mf(tmp_path, extruders=(1, 2))
    config = zipfile.ZipFile(project).read(MODEL_CONFIG).decode()
    config = config.replace('   <metadata type="volume" key="extruder" value="2"/>\n', "")
    config = config.replace(
        "  <volume", '  <metadata type="object" key="extruder" value="2"/>\n  <volume', 1
    )
    _replace_member(project, MODEL_CONFIG, config)
    assert inspect_plate(project).extruders == frozenset({1, 2})
    with pytest.raises(PreflightError, match="extruders 1, 2"):
        check_plate(project)


def test_the_mesh_is_not_held_in_memory_to_read_the_build_section(tmp_path):
    """The tag preflight wants is at the end of the model, and the mesh above it can be huge."""
    source, destination = fixture("cylinder_6mm.3mf"), tmp_path / "big.3mf"
    model = zipfile.ZipFile(source).read(MODEL_FILE).decode()
    head, tail = model.split("<build>", 1)
    mesh = '   <vertex x="1.234567" y="2.345678" z="3.456789"/>\n' * 600_000
    shutil.copyfile(source, destination)
    _replace_member(destination, MODEL_FILE, head + mesh + "<build>" + tail)

    tracemalloc.start()
    try:
        plate = inspect_plate(destination)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert (plate.objects, plate.instances) == (1, 1)
    assert peak < len(mesh) / 2
