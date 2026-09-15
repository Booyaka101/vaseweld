from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import vaseweld.slicer
from conftest import corrupt_member, fixture, project_3mf
from vaseweld.slicer import (
    NORMAL_OVERRIDES,
    SPIRAL_VASE_OVERRIDES,
    Slicer,
    SlicerError,
    _windows_candidates,
    dropped_supports,
    find_slicer,
    merged_config,
    normal_overrides,
    probe_slicer,
    run_slice,
    slicer_argv,
    unrecoverable_vase,
    version_complaint,
)

PROJECT = "cylinder_6mm.3mf"
VERIFIED = Slicer(path=Path("prusa-slicer"), banner="", release=(2, 9, 6))


def test_the_version_banner_is_read_from_help(fake_slicer):
    found = probe_slicer(fake_slicer.path)
    assert found.release == (2, 9, 6)
    assert found.version == "2.9.6"
    assert fake_slicer.calls == [[str(fake_slicer.path), "--help"]]


def test_a_platform_suffix_stays_on_the_version(fake_slicer):
    fake_slicer.banner = "PrusaSlicer-2.9.6+win64 based on Slic3r"
    assert probe_slicer(fake_slicer.path).version == "2.9.6+win64"


def test_something_that_is_not_prusaslicer_has_no_release(fake_slicer):
    fake_slicer.banner = "OrcaSlicer 2.4.2"
    found = probe_slicer(fake_slicer.path)
    assert found.release is None
    assert found.version == "unknown version"
    complaint = version_complaint(found)
    assert "does not identify itself as PrusaSlicer" in complaint.message
    assert complaint.fatal


@pytest.mark.parametrize(
    "release, fatal, expected",
    [
        pytest.param((2, 9, 6), False, None, id="verified"),
        pytest.param((2, 9, 0), False, None, id="same-series"),
        pytest.param((2, 8, 1), False, "older than the 2.9.x", id="older"),
        pytest.param((3, 0, 0), True, "rewrote its command line", id="refactored"),
        pytest.param((3, 1, 0), True, "rewrote its command line", id="later-still"),
    ],
)
def test_the_version_gate_classifies_each_series(release, fatal, expected):
    found = Slicer(path=Path("prusa-slicer"), banner="", release=release)
    complaint = version_complaint(found)
    if expected is None:
        assert complaint is None
    else:
        assert expected in complaint.message
        assert complaint.fatal is fatal


def test_the_spiral_vase_overrides_are_the_set_prusaslicers_gui_applies():
    # ConfigManipulation.cpp toggles these six alongside spiral_vase, from a dialog
    # that never runs headless.
    assert SPIRAL_VASE_OVERRIDES == (
        "--spiral-vase=1",
        "--perimeters=1",
        "--top-solid-layers=0",
        "--fill-density=0",
        "--support-material=0",
        "--support-material-enforce-layers=0",
        "--thin-walls=0",
    )


def test_the_command_line_for_one_pass_is_exact(tmp_path):
    argv = slicer_argv(
        VERIFIED,
        tmp_path / "vase.3mf",
        tmp_path / "out.gcode",
        overrides=SPIRAL_VASE_OVERRIDES,
        load=(tmp_path / "print.ini", tmp_path / "printer.ini"),
    )
    assert argv == [
        "prusa-slicer",
        "--export-gcode",
        "--load",
        str(tmp_path / "print.ini"),
        "--load",
        str(tmp_path / "printer.ini"),
        "--spiral-vase=1",
        "--perimeters=1",
        "--top-solid-layers=0",
        "--fill-density=0",
        "--support-material=0",
        "--support-material-enforce-layers=0",
        "--thin-walls=0",
        "--output",
        str(tmp_path / "out.gcode"),
        str(tmp_path / "vase.3mf"),
    ]


def test_the_normal_pass_overrides_nothing(tmp_path):
    argv = slicer_argv(VERIFIED, tmp_path / "vase.3mf", tmp_path / "out.gcode")
    assert argv == [
        "prusa-slicer",
        "--export-gcode",
        "--output",
        str(tmp_path / "out.gcode"),
        str(tmp_path / "vase.3mf"),
    ]


def test_an_explicit_path_is_used_as_given(fake_slicer):
    assert find_slicer(fake_slicer.path) == fake_slicer.path


def test_an_explicit_directory_is_searched_for_the_binary(fake_slicer):
    assert find_slicer(fake_slicer.path.parent) == fake_slicer.path


@pytest.mark.parametrize(
    "platform, wanted",
    [
        ("win32", "prusa-slicer-console.exe"),
        ("darwin", "PrusaSlicer.app, or the Contents/MacOS/PrusaSlicer inside it"),
        ("linux", "prusa-slicer."),
    ],
)
def test_an_explicit_directory_without_a_binary_says_which_name_it_wanted(
    tmp_path, monkeypatch, platform, wanted
):
    """The hint is per platform, so asserting the Windows one passes only on Windows."""
    monkeypatch.setattr(vaseweld.slicer.sys, "platform", platform)
    with pytest.raises(SlicerError) as excinfo:
        find_slicer(tmp_path)
    assert "no PrusaSlicer binary in it" in str(excinfo.value)
    assert wanted in str(excinfo.value)


def test_an_explicit_path_that_does_not_exist_names_the_flag(tmp_path):
    with pytest.raises(SlicerError, match="Check --slicer-path"):
        find_slicer(tmp_path / "nope.exe")


def test_a_pass_that_writes_nothing_is_a_failure_even_at_exit_zero(fake_slicer, tmp_path):
    # 2.9.6 exits 0 after "All objects are outside of the print volume".
    fake_slicer.normal = None
    fake_slicer.stderr = "All objects are outside of the print volume.\n"
    with pytest.raises(SlicerError) as excinfo:
        run_slice(probe_slicer(fake_slicer.path), tmp_path / "vase.3mf", tmp_path / "out.gcode")
    message = str(excinfo.value)
    assert "produced no G-code for vase.3mf (exit 0)" in message
    assert "All objects are outside of the print volume." in message


def test_a_silent_failure_says_to_run_it_by_hand(fake_slicer, tmp_path):
    fake_slicer.normal = None
    fake_slicer.returncode = 1
    with pytest.raises(SlicerError, match="by hand"):
        run_slice(probe_slicer(fake_slicer.path), tmp_path / "vase.3mf", tmp_path / "out.gcode")


def test_progress_lines_are_not_mistaken_for_the_reason(fake_slicer, tmp_path):
    fake_slicer.normal = None
    fake_slicer.stderr = "Slicing model\n30 => Generating perimeters\n45 => Preparing infill\n"
    with pytest.raises(SlicerError) as excinfo:
        run_slice(probe_slicer(fake_slicer.path), tmp_path / "vase.3mf", tmp_path / "out.gcode")
    assert str(excinfo.value).endswith("Slicing model")


def test_a_timeout_points_at_the_flag_that_raises_it(fake_slicer, tmp_path, monkeypatch):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 5)

    found = probe_slicer(fake_slicer.path)
    monkeypatch.setattr(vaseweld.slicer.subprocess, "run", timeout)
    with pytest.raises(SlicerError) as excinfo:
        run_slice(found, tmp_path / "vase.3mf", tmp_path / "out.gcode", timeout=5)
    assert "did not finish within 5s" in str(excinfo.value)
    assert "--slicer-timeout" in str(excinfo.value)


def test_a_successful_pass_returns_the_file_it_wrote(fake_slicer, tmp_path):
    destination = tmp_path / "out.gcode"
    written = run_slice(probe_slicer(fake_slicer.path), tmp_path / "cyl.3mf", destination)
    assert written == destination
    assert destination.read_bytes() == fixture("prusaslicer_normal_6mm.gcode").read_bytes()


def test_the_windowless_windows_binary_is_named_in_the_complaint(monkeypatch, tmp_path):
    monkeypatch.setattr(vaseweld.slicer.sys, "platform", "win32")
    found = Slicer(path=tmp_path / "prusa-slicer.exe", banner="", release=None)
    complaint = version_complaint(found)
    assert "it printed nothing" in complaint.message
    assert complaint.message.endswith("point --slicer-path at prusa-slicer-console.exe instead.")


def test_verbose_shows_a_command_line_you_could_paste_back(fake_slicer, tmp_path, capsys):
    found = probe_slicer(fake_slicer.path)
    run_slice(found, fixture("cylinder_6mm.3mf"), tmp_path / "out.gcode", echo=sys.stdout)
    shown = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  $ ")]
    assert len(shown) == 1
    assert str(fake_slicer.path) in shown[0]
    assert shown[0].endswith("cylinder_6mm.3mf") or shown[0].endswith('cylinder_6mm.3mf"')


def test_a_leftover_file_does_not_count_as_a_successful_pass(fake_slicer, tmp_path, capsys):
    """--keep-slices into the same directory twice must not weld the previous run's output."""
    destination = tmp_path / "out.gcode"
    destination.write_text("; left over from the run before\n", encoding="utf-8")
    fake_slicer.normal = None
    fake_slicer.returncode = 1
    fake_slicer.stderr = "Error: The supplied file could not be read\n"
    found = probe_slicer(fake_slicer.path)
    with pytest.raises(SlicerError) as excinfo:
        run_slice(found, fixture("cylinder_6mm.3mf"), destination)
    assert "could not be read" in str(excinfo.value)
    assert not destination.exists()


def test_the_normal_pass_turns_spiral_vase_off_rather_than_leaving_it(fake_slicer, tmp_path):
    found = probe_slicer(fake_slicer.path)
    argv = slicer_argv(
        found, fixture("cylinder_6mm.3mf"), tmp_path / "out.gcode", overrides=NORMAL_OVERRIDES
    )
    assert NORMAL_OVERRIDES == ("--spiral-vase=0",)
    assert "--spiral-vase=0" in argv


def test_a_newer_series_is_not_called_older(tmp_path):
    complaint = version_complaint(Slicer(path=tmp_path / "ps", banner="", release=(2, 10, 0)))
    assert "2.10.0 is newer than the 2.9.x" in complaint.message
    assert not complaint.fatal


def test_a_vase_project_hands_back_what_normalize_would_eat(tmp_path):
    """--spiral-vase=0 alone is not enough: 2.9.6 normalizes the config before overrides land."""
    project = project_3mf(
        tmp_path,
        "vase.3mf",
        spiral_vase="1",
        perimeters="7",
        top_solid_layers="4",
        fill_density="35%",
    )
    assert normal_overrides(merged_config(project)) == (
        "--spiral-vase=0",
        "--perimeters=7",
        "--top-solid-layers=4",
        "--fill-density=35%",
        "--retract-layer-change=0",
        "--filament-retract-layer-change=nil",
    )


def test_a_project_that_was_never_a_vase_is_handed_back_its_own_settings(tmp_path):
    """Restoring runs unconditionally, so a plain project gets its own values and the defaults."""
    project = project_3mf(tmp_path, "plain.3mf", spiral_vase="0", perimeters="7")
    assert normal_overrides(merged_config(project)) == (
        "--spiral-vase=0",
        "--perimeters=7",
        "--top-solid-layers=3",
        "--fill-density=20%",
        "--retract-layer-change=0",
        "--filament-retract-layer-change=nil",
    )


def test_a_load_ini_wins_over_the_project_the_way_prusaslicer_reads_them(tmp_path):
    project = project_3mf(tmp_path, "vase.3mf", spiral_vase="1", perimeters="7")
    ini = tmp_path / "normal.ini"
    ini.write_text("spiral_vase = 0\nperimeters = 2\n", encoding="utf-8")
    overrides = normal_overrides(merged_config(project, (ini,)))
    assert "--perimeters=2" in overrides
    assert "--perimeters=7" not in overrides


def test_a_vase_ini_over_a_plain_project_restores_the_projects_values(tmp_path):
    project = project_3mf(tmp_path, "plain.3mf", spiral_vase="0", perimeters="7")
    ini = tmp_path / "vase.ini"
    ini.write_text("spiral_vase = 1\n", encoding="utf-8")
    overrides = normal_overrides(merged_config(project, (ini,)))
    assert "--perimeters=7" in overrides
    # never named in either file, so the only honest answer is PrusaSlicer's own default
    assert "--fill-density=20%" in overrides
    off = tmp_path / "off.ini"
    off.write_text("spiral_vase = 0\n", encoding="utf-8")
    # normalize_fdm ran as vase.ini was read, so the mode being off by the end changes nothing
    assert normal_overrides(merged_config(project, (ini, off))) == overrides


def test_a_project_saved_with_the_checkbox_on_says_what_cannot_be_recovered(tmp_path):
    saved = project_3mf(
        tmp_path,
        "saved.3mf",
        spiral_vase="1",
        perimeters="1",
        top_solid_layers="0",
        fill_density="0%",
    )
    warning = unrecoverable_vase(merged_config(saved))
    assert warning is not None
    assert "Untick Spiral Vase" in warning
    intact = project_3mf(tmp_path, "intact.3mf", spiral_vase="1", perimeters="3")
    assert unrecoverable_vase(merged_config(intact)) is None


def test_an_ini_that_only_turns_the_mode_off_does_not_recover_the_settings(tmp_path):
    """Measured on 2.9.6: this pair slices at perimeters = 1, with no perimeter or infill sections."""
    saved = project_3mf(
        tmp_path,
        "saved_vase.3mf",
        spiral_vase="1",
        perimeters="1",
        top_solid_layers="0",
        fill_density="0%",
    )
    off = tmp_path / "off.ini"
    off.write_text("spiral_vase = 0\n", encoding="utf-8")
    config = merged_config(saved, (off,))
    assert config["spiral_vase"] == "0"
    assert unrecoverable_vase(config) is not None


def test_a_profile_with_supports_on_is_told_the_spiral_pass_cannot_have_them(tmp_path):
    """Measured on 2.9.6: supports on move the Zs, and both passes have to agree on those."""
    plain = project_3mf(tmp_path, "plain.3mf", support_material="0")
    assert dropped_supports(merged_config(plain)) is None
    for key in ("support_material", "support_material_enforce_layers"):
        project = project_3mf(tmp_path, f"{key}.3mf", **{key: "1"})
        warning = dropped_supports(merged_config(project))
        assert warning is not None
        assert "refuses to slice spiral vase with supports" in warning


def test_a_project_whose_config_will_not_decompress_is_read_as_having_none(tmp_path):
    """Preflight passes when only this member is damaged, so merged_config has to survive it."""
    project = project_3mf(tmp_path, "damaged.3mf", spiral_vase="1", perimeters="7")
    assert merged_config(corrupt_member(project, "Metadata/Slic3r_PE.config")) == {}


def test_a_model_with_no_config_in_it_asks_for_the_slicers_own_defaults(tmp_path):
    """A plain mesh has no print settings, and a binary STL must not read as any.

    Measured on 2.9.6: slicing with no config at all and slicing with these five passed
    explicitly give a 0-line body diff, which is what makes restoring unconditionally safe.
    """
    assert merged_config(fixture("cylinder_6mm.3mf")) == {}
    stl = tmp_path / "cylinder.stl"
    stl.write_bytes(bytes([0]) + b"solid = nonsense" + bytes(range(256)) * 4)
    assert normal_overrides(merged_config(stl)) == (
        "--spiral-vase=0",
        "--perimeters=3",
        "--top-solid-layers=3",
        "--fill-density=20%",
        "--retract-layer-change=0",
        "--filament-retract-layer-change=nil",
    )


def test_a_macos_app_bundle_is_a_slicer_path_worth_accepting(tmp_path):
    binary = tmp_path / "PrusaSlicer.app" / "Contents" / "MacOS" / "PrusaSlicer"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    assert find_slicer(tmp_path / "PrusaSlicer.app") == binary


def test_a_newer_install_is_tried_before_an_older_one(tmp_path, monkeypatch):
    """2.10.0 ships eventually, and sorting the directory names as strings puts 2.9.6 first."""
    for name in ("PrusaSlicer-2.9.6+win64", "PrusaSlicer-2.10.0+win64"):
        (tmp_path / name).mkdir()
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    for other in ("ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        monkeypatch.setenv(other, "")
    names = [path.parent.name for path in _windows_candidates()]
    assert names.index("PrusaSlicer-2.10.0+win64") < names.index("PrusaSlicer-2.9.6+win64")


def test_an_unset_localappdata_does_not_glob_the_working_directory(tmp_path, monkeypatch):
    """os.path.join("", "Programs") is a truthy relative path, and glob would follow it from cwd."""
    (tmp_path / "Programs" / "PrusaSlicer-2.9.6").mkdir(parents=True)
    for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        monkeypatch.setenv(name, "")
    monkeypatch.chdir(tmp_path)
    assert _windows_candidates() == []


def test_a_commented_out_line_in_an_ini_is_not_a_setting(tmp_path):
    """PrusaSlicer treats ";" as a comment in an ini, so reading one as live would fight it."""
    ini = tmp_path / "print.ini"
    ini.write_text(
        "spiral_vase = 1\nperimeters = 4\n; perimeters = 1\n# perimeters = 2\n", encoding="utf-8"
    )
    assert merged_config(fixture("cylinder_6mm.3mf"), (ini,))["perimeters"] == "4"
    assert "--perimeters=4" in normal_overrides(merged_config(fixture("cylinder_6mm.3mf"), (ini,)))


def test_the_project_block_still_needs_its_semicolons(tmp_path):
    project = project_3mf(tmp_path, "vase.3mf", spiral_vase="1", perimeters="7")
    assert merged_config(project)["perimeters"] == "7"


def test_layer_change_retraction_is_put_back_too(tmp_path):
    """normalize_fdm turns it off with the rest, and nothing downstream turns it on again."""
    ini = tmp_path / "vase.ini"
    ini.write_text("spiral_vase = 1\nretract_layer_change = 1\n", encoding="utf-8")
    assert "--retract-layer-change=1" in normal_overrides(merged_config(fixture(PROJECT), (ini,)))


def test_the_filament_level_retraction_override_is_put_back_as_well(tmp_path):
    """It beats retract_layer_change where it is set, and normalize turns it off too."""
    ini = tmp_path / "vase.ini"
    ini.write_text(
        "spiral_vase = 1\nretract_layer_change = 1\nfilament_retract_layer_change = 1\n",
        encoding="utf-8",
    )
    assert "--filament-retract-layer-change=1" in normal_overrides(
        merged_config(fixture(PROJECT), (ini,))
    )


def test_a_filament_override_the_config_never_set_is_handed_back_as_nil(tmp_path):
    """Leaving it out leaves normalize's 0 in place, and 0 at the filament level wins.

    Measured on 2.9.6 over cylinder_40mm: without this the normal pass differs from a
    plain slice of the same profile by 405 lines, a retraction and its unretract at
    every layer change. "nil" is the spelling for the unset the profile had.
    """
    ini = tmp_path / "vase.ini"
    ini.write_text("spiral_vase = 1\nretract_layer_change = 1\n", encoding="utf-8")
    overrides = normal_overrides(merged_config(fixture(PROJECT), (ini,)))
    assert "--filament-retract-layer-change=nil" in overrides


def test_a_bare_mesh_is_never_opened_looking_for_settings(tmp_path, monkeypatch):
    """A .stl is geometry, and reading 100 MB of it to find no keys is pure waste."""
    stl = tmp_path / "huge.stl"
    stl.write_bytes(b"not read")
    monkeypatch.setattr(
        Path, "read_text", lambda *a, **k: pytest.fail("the mesh should not be read")
    )
    assert merged_config(stl) == {}
