from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import vaseweld.slicer
from conftest import FakePrusaSlicer, example, fixture, multi_material_3mf, project_3mf
from vaseweld.cli import EXIT_OK, EXIT_USAGE, _ladder_divergence, _SliceDir, main
from vaseweld.parser import parse_file
from vaseweld.slicer import DOWNLOAD_URL, SPIRAL_VASE_OVERRIDES

PROJECT = "cylinder_6mm.3mf"


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err.splitlines()


def auto_argv(fake, output, project=PROJECT, at="3", extra=()):
    """Build an `auto` argv. A str names a fixture; a Path is used verbatim."""
    return [
        "auto",
        str(project if isinstance(project, Path) else fixture(project)),
        "--slicer-path",
        str(fake.path),
        "--at",
        at,
        *extra,
        "-o",
        str(output),
    ]


def test_a_missing_slicer_names_the_download_page(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(vaseweld.slicer.shutil, "which", lambda name: None)
    monkeypatch.setattr(vaseweld.slicer, "slicer_candidates", list)
    out = tmp_path / "out.gcode"
    code, _, stderr = run(["auto", str(fixture(PROJECT)), "--at", "3", "-o", str(out)], capsys)
    assert code == EXIT_USAGE
    assert stderr == [
        f"vaseweld: PrusaSlicer not found. Pass --slicer-path, or install it from {DOWNLOAD_URL}"
    ]
    assert not out.exists()


def test_a_verified_slicer_slices_twice_and_welds(fake_slicer, tmp_path, capsys):
    out = tmp_path / "out.gcode"
    code, stdout, _ = run(auto_argv(fake_slicer, out), capsys)
    assert code == EXIT_OK
    assert stdout[:7] == [
        f"slicer: {fake_slicer.path} (2.9.6)",
        "cylinder_6mm.3mf: 1 object",
        "pass 1/2 normal",
        "pass 2/2 spiral vase",
        "cylinder_6mm.3mf: 30 layers, Z 0.200 to 6.000",
        "layer height: 0.200 mm",
        "weldable range: Z 0.400 to 6.000 (layers 2 to 30)",
    ]
    assert stdout[7] == "cut snapped to Z=3.000 (layer 15)"
    written = len(out.read_text(encoding="utf-8").splitlines())
    assert stdout[-1] == f"wrote {out} ({written} lines)"

    code, checked, _ = run(["check", str(out)], capsys)
    assert code == EXIT_OK
    assert checked == [
        "OK: Z monotonic, E coherent (relative), retractions balanced, 1 temperature timeline"
    ]


def test_both_passes_run_the_exact_command_line(fake_slicer, tmp_path, capsys):
    config = tmp_path / "print.ini"
    config.write_text("layer_height = 0.2\n", encoding="utf-8")
    run(auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--load", str(config)]), capsys)
    normal, vase = fake_slicer.slices

    assert normal[:4] == [str(fake_slicer.path), "--export-gcode", "--load", str(config)]
    assert tuple(normal[4:10]) == (
        "--spiral-vase=0",
        "--perimeters=3",
        "--top-solid-layers=3",
        "--fill-density=20%",
        "--retract-layer-change=0",
        "--filament-retract-layer-change=nil",
    )
    assert normal[10] == "--output"
    assert normal[11].endswith("cylinder_6mm-normal.gcode")
    assert normal[12] == str(fixture(PROJECT))
    assert len(normal) == 13

    assert vase[:4] == normal[:4]
    assert tuple(vase[4:11]) == SPIRAL_VASE_OVERRIDES
    assert vase[11] == "--output"
    assert vase[12].endswith("cylinder_6mm-spiral.gcode")
    assert vase[13] == str(fixture(PROJECT))
    assert len(vase) == 14


def test_a_refactored_slicer_is_refused_before_any_slicing(fake_slicer, tmp_path, capsys):
    fake_slicer.banner = "PrusaSlicer-3.0.0-alpha11 based on Slic3r"
    out = tmp_path / "out.gcode"
    code, _, stderr = run(auto_argv(fake_slicer, out), capsys)
    assert code == EXIT_USAGE
    assert len(stderr) == 1
    assert "PrusaSlicer 3.0.0-alpha11 rewrote its command line" in stderr[0]
    assert stderr[0].endswith("Pass --force-slicer-version to run anyway.")
    assert fake_slicer.slices == []
    assert not out.exists()


def test_force_slicer_version_runs_against_the_refactored_build(fake_slicer, tmp_path, capsys):
    fake_slicer.banner = "PrusaSlicer-3.0.0-alpha11 based on Slic3r"
    out = tmp_path / "out.gcode"
    code, stdout, stderr = run(
        auto_argv(fake_slicer, out, extra=["--force-slicer-version"]), capsys
    )
    assert code == EXIT_OK
    assert "rewrote its command line" in stderr[0]
    assert "--force-slicer-version" not in stderr[0]
    assert stdout[0] == f"slicer: {fake_slicer.path} (3.0.0-alpha11)"
    assert out.exists()


def test_an_older_series_warns_and_carries_on(fake_slicer, tmp_path, capsys):
    fake_slicer.banner = "PrusaSlicer-2.8.1+win64 based on Slic3r"
    out = tmp_path / "out.gcode"
    code, _, stderr = run(auto_argv(fake_slicer, out), capsys)
    assert code == EXIT_OK
    assert "older than the 2.9.x" in stderr[0]
    assert out.exists()


def test_two_copies_of_one_object_are_refused_before_slicing(fake_slicer, tmp_path, capsys):
    out = tmp_path / "out.gcode"
    code, _, stderr = run(auto_argv(fake_slicer, out, project="two_objects.3mf"), capsys)
    assert code == EXIT_USAGE
    assert "2 copies of one object" in stderr[0]
    assert fake_slicer.calls == []
    assert not out.exists()


def test_two_extruders_are_refused_before_slicing(fake_slicer, tmp_path, capsys):
    project = multi_material_3mf(tmp_path)
    out = tmp_path / "out.gcode"
    code, _, stderr = run(
        ["auto", str(project), "--slicer-path", str(fake_slicer.path), "--at", "3", "-o", str(out)],
        capsys,
    )
    assert code == EXIT_USAGE
    assert "extruders 1, 2" in stderr[0]
    assert fake_slicer.calls == []
    assert not out.exists()


def test_slices_at_different_layer_heights_abort_instead_of_welding(fake_slicer, tmp_path, capsys):
    fake_slicer.vase = "mismatch_layerheight_6mm.gcode"
    out = tmp_path / "out.gcode"
    code, _, stderr = run(auto_argv(fake_slicer, out), capsys)
    assert code == EXIT_USAGE
    assert "disagree about layer Z" in stderr[0]
    assert "Layer 1 is Z 0.200 in the normal pass and Z 0.300 in the spiral vase pass" in stderr[0]
    assert "fixed layer height" in stderr[0]
    assert not out.exists()


def test_the_abort_blames_supports_when_the_normal_pass_had_them():
    """Supports are what moved the Zs, and the spiral pass is never allowed to keep them."""
    normal = parse_file(fixture("prusaslicer_normal_6mm.gcode"))
    vase = parse_file(fixture("mismatch_layerheight_6mm.gcode"))
    assert "fixed layer height" in _ladder_divergence(normal, vase)
    normal.config["support_material"] = "1"
    message = _ladder_divergence(normal, vase)
    assert "sliced with support material" in message
    assert "fixed layer height" not in message


def test_a_profile_with_supports_on_is_warned_about_before_either_pass(
    fake_slicer, tmp_path, capsys
):
    """Two passes take minutes and this one will almost certainly be refused after them."""
    project = project_3mf(tmp_path, "supported.3mf", support_material="1")
    argv = auto_argv(fake_slicer, tmp_path / "out.gcode", project=project)
    code, _, stderr = run(argv, capsys)
    assert code == EXIT_OK
    assert "refuses to slice spiral vase with supports" in stderr[0]


def test_a_second_pass_of_a_different_length_aborts_too(fake_slicer, tmp_path, capsys):
    fake_slicer.vase = "prusaslicer_vase_40mm.gcode"
    code, _, stderr = run(auto_argv(fake_slicer, tmp_path / "out.gcode"), capsys)
    assert code == EXIT_USAGE
    assert "30 layers and the spiral vase pass 200" in stderr[0]


def test_the_output_defaults_to_a_name_beside_the_project(fake_slicer, tmp_path, capsys):
    project = tmp_path / "cylinder_6mm.3mf"
    shutil.copyfile(fixture(PROJECT), project)
    code, stdout, _ = run(
        ["auto", str(project), "--slicer-path", str(fake_slicer.path), "--at", "3"], capsys
    )
    assert code == EXIT_OK
    written = tmp_path / "cylinder_6mm-vaseweld.gcode"
    assert written.exists()
    assert stdout[-1].startswith(f"wrote {written} (")


def test_keep_slices_leaves_both_passes_on_disk(fake_slicer, tmp_path, capsys):
    kept = tmp_path / "slices"
    code, stdout, _ = run(
        auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--keep-slices", str(kept)]), capsys
    )
    assert code == EXIT_OK
    assert sorted(p.name for p in kept.iterdir()) == [
        "cylinder_6mm-normal.gcode",
        "cylinder_6mm-spiral.gcode",
    ]
    assert f"keeping both slices in {kept}" in stdout


def test_the_temporary_directory_does_not_survive(fake_slicer, tmp_path, capsys):
    run(auto_argv(fake_slicer, tmp_path / "out.gcode"), capsys)
    workdir = os.path.dirname(fake_slicer.slices[0][-2])
    assert not os.path.exists(workdir)


def test_verbose_echoes_what_prusaslicer_printed(fake_slicer, tmp_path, capsys):
    fake_slicer.stderr = "Slicing model\nExporting G-code\n"
    code, stdout, _ = run(
        auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--verbose"]), capsys
    )
    assert code == EXIT_OK
    assert stdout.count("  Slicing model") == 2
    assert stdout.count("  Exporting G-code") == 2


def test_a_missing_config_names_the_flag(fake_slicer, tmp_path, capsys):
    code, _, stderr = run(
        auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--load", str(tmp_path / "no.ini")]),
        capsys,
    )
    assert code == EXIT_USAGE
    assert "no such config file" in stderr[0]
    assert "--load" in stderr[0]
    assert fake_slicer.calls == []


def test_auto_will_not_write_binary_gcode(fake_slicer, tmp_path, capsys):
    out = tmp_path / "out.bgcode"
    code, _, stderr = run(auto_argv(fake_slicer, out), capsys)
    assert code == EXIT_USAGE
    assert "cannot write it" in stderr[0]
    assert not out.exists()


def test_a_flow_ratio_out_of_range_is_caught_before_slicing(fake_slicer, tmp_path, capsys):
    code, _, stderr = run(
        auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--start-flow", "1.5"]), capsys
    )
    assert code == EXIT_USAGE
    assert "between 0 and 1" in stderr[0]
    assert fake_slicer.calls == []


def test_two_cuts_alternate_the_same_way_weld_does(fake_slicer, tmp_path, capsys):
    code, stdout, _ = run(
        auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--at", "4.6"]), capsys
    )
    assert code == EXIT_OK
    assert stdout[7] == "cuts snapped to Z=3.000 (layer 15), Z=4.600 (layer 23)"


def test_a_bare_model_file_is_taken_on_trust(fake_slicer, tmp_path, capsys):
    code, stdout, _ = run(
        [
            "auto",
            str(example("cylinder_6mm.stl")),
            "--slicer-path",
            str(fake_slicer.path),
            "--at",
            "3",
            "-o",
            str(tmp_path / "out.gcode"),
        ],
        capsys,
    )
    assert code == EXIT_OK
    assert stdout[1] == "cylinder_6mm.stl: single mesh"


@pytest.mark.skipif(
    not os.environ.get("VASEWELD_E2E"),
    reason="set VASEWELD_E2E=1 with PrusaSlicer 2.9.x installed to run the real thing",
)
def test_end_to_end_against_the_real_prusaslicer(tmp_path, capsys):
    out = tmp_path / "hybrid.gcode"
    argv = ["auto", str(example("vase.3mf")), "--at", "6.0", "-o", str(out)]
    path = os.environ.get("VASEWELD_SLICER")
    if path:
        argv += ["--slicer-path", path]
    code, stdout, _ = run(argv, capsys)
    assert code == EXIT_OK
    assert stdout[0].startswith("slicer: ")
    assert stdout[2:4] == ["pass 1/2 normal", "pass 2/2 spiral vase"]
    assert out.is_file()

    code, checked, _ = run(["check", str(out)], capsys)
    assert code == EXIT_OK
    assert checked[0].startswith("OK: ")


def test_the_fake_is_not_quietly_a_real_install(fake_slicer):
    """If this ever fails, every test above it may have been driving the real binary."""
    assert isinstance(fake_slicer, FakePrusaSlicer)
    assert fake_slicer.path.read_text(encoding="utf-8") == "stand-in for prusa-slicer\n"


def test_a_project_saved_with_spiral_vase_on_still_gets_a_solid_base(fake_slicer, tmp_path, capsys):
    """Without --spiral-vase=0 the normal pass inherits it and both passes come out a vase."""
    code, _, _ = run(auto_argv(fake_slicer, tmp_path / "out.gcode"), capsys)
    assert code == EXIT_OK
    normal, vase = fake_slicer.slices
    assert "--spiral-vase=0" in normal
    assert "--spiral-vase=1" in vase


def test_a_second_run_into_the_same_kept_directory_does_not_reuse_the_first(
    fake_slicer, tmp_path, capsys
):
    kept = tmp_path / "slices"
    extra = ["--keep-slices", str(kept)]
    assert run(auto_argv(fake_slicer, tmp_path / "a.gcode", extra=extra), capsys)[0] == EXIT_OK

    fake_slicer.normal = None
    fake_slicer.returncode = 1
    fake_slicer.stderr = "Error: The supplied file could not be read\n"
    code, _, stderr = run(auto_argv(fake_slicer, tmp_path / "b.gcode", extra=extra), capsys)
    assert code == EXIT_USAGE
    assert "could not be read" in stderr[0]
    assert not (tmp_path / "b.gcode").exists()


def test_an_output_directory_that_is_not_there_is_caught_before_slicing(
    fake_slicer, tmp_path, capsys
):
    """Two slicing passes take minutes. Finding out afterwards that -o cannot be written is waste."""
    argv = auto_argv(fake_slicer, tmp_path / "nope" / "out.gcode")
    code, _, stderr = run(argv, capsys)
    assert code == EXIT_USAGE
    assert "no such directory" in stderr[0]
    assert fake_slicer.slices == []


@pytest.mark.parametrize("timeout", ["nan", "0", "-5"])
def test_a_timeout_that_cannot_work_never_launches_the_slicer(fake_slicer, tmp_path, timeout):
    """nan reaches subprocess.run and raises; 0 and anything below start a pass to kill it."""
    argv = auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--slicer-timeout", timeout])
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == EXIT_USAGE
    assert fake_slicer.calls == []


def test_a_file_left_open_in_the_temp_dir_does_not_break_a_finished_run():
    """Windows will not delete an open file, and cleanup must not turn a written weld into a crash."""
    with _SliceDir(None) as workdir:
        held = (workdir / "held.gcode").open("w", encoding="utf-8")
    held.close()


def test_a_path_with_no_name_is_refused_like_any_other_directory(fake_slicer, capsys):
    """`.` has no stem, and the default output name used to be built from it before preflight ran."""
    argv = ["auto", ".", "--slicer-path", str(fake_slicer.path), "--at", "3"]
    code, _, stderr = run(argv, capsys)
    assert code == EXIT_USAGE
    assert "is a directory, not a model" in stderr[0]
    assert fake_slicer.calls == []


def test_writing_over_the_project_is_refused_before_slicing(fake_slicer, tmp_path, capsys):
    """The project is the only copy of the model, and -o would land G-code on top of it."""
    project = tmp_path / "vase.3mf"
    shutil.copyfile(fixture(PROJECT), project)
    code, _, stderr = run(auto_argv(fake_slicer, project, project=project), capsys)
    assert code == EXIT_USAGE
    assert "is the project itself" in stderr[0]
    assert project.read_bytes() == fixture(PROJECT).read_bytes()
    assert fake_slicer.slices == []


def test_an_output_that_is_a_directory_is_caught_before_slicing(fake_slicer, tmp_path, capsys):
    existing = tmp_path / "out"
    existing.mkdir()
    code, _, stderr = run(auto_argv(fake_slicer, existing), capsys)
    assert code == EXIT_USAGE
    assert "is a directory" in stderr[0]
    assert fake_slicer.slices == []


def test_two_passes_in_the_same_mode_are_refused_rather_than_welded(fake_slicer, tmp_path, capsys):
    """If PrusaSlicer ever ignored the override, the ladders would still match."""
    fake_slicer.normal = fake_slicer.vase
    code, _, stderr = run(auto_argv(fake_slicer, tmp_path / "out.gcode"), capsys)
    assert code == EXIT_USAGE
    assert "both passes were sliced as a vase" in stderr[0]
    assert "--verbose" in stderr[0]


def test_passes_that_come_out_the_wrong_way_round_are_described_that_way(
    fake_slicer, tmp_path, capsys
):
    """Neither pass is "both", and saying spiral vase is off beside spiral_vase=1 helps nobody."""
    fake_slicer.normal, fake_slicer.vase = fake_slicer.vase, fake_slicer.normal
    code, _, stderr = run(auto_argv(fake_slicer, tmp_path / "out.gcode"), capsys)
    assert code == EXIT_USAGE
    assert "the normal pass came out as a vase" in stderr[0]
    assert "spiral_vase=1 and 0" in stderr[0]


def test_the_abort_points_at_the_slices_when_they_were_already_kept(fake_slicer, tmp_path, capsys):
    """Telling someone who passed --keep-slices to re-run with --keep-slices helps nobody."""
    fake_slicer.vase = "mismatch_layerheight_6mm.gcode"
    kept = tmp_path / "slices"
    argv = auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--keep-slices", str(kept)])
    code, stdout, stderr = run(argv, capsys)
    assert code == EXIT_USAGE
    assert f"keeping both slices in {kept}" in stdout
    assert f"Both passes are in {kept}." in stderr[0]
    assert "Re-run with --keep-slices" not in stderr[0]


def test_the_abort_suggests_keeping_them_when_they_were_not(fake_slicer, tmp_path, capsys):
    fake_slicer.vase = "mismatch_layerheight_6mm.gcode"
    code, _, stderr = run(auto_argv(fake_slicer, tmp_path / "out.gcode"), capsys)
    assert code == EXIT_USAGE
    assert "Re-run with --keep-slices to look at both passes." in stderr[0]


def test_a_vase_project_slices_its_normal_pass_with_the_settings_restored(
    fake_slicer, tmp_path, capsys
):
    project = project_3mf(
        tmp_path, "vase.3mf", spiral_vase="1", perimeters="7", top_solid_layers="4"
    )
    argv = auto_argv(fake_slicer, tmp_path / "out.gcode", project=project)
    assert run(argv, capsys)[0] == EXIT_OK
    normal, vase = fake_slicer.slices
    assert "--perimeters=7" in normal
    assert "--top-solid-layers=4" in normal
    assert "--spiral-vase=0" in normal
    assert "--perimeters=1" in vase


def test_a_failed_pass_still_says_where_the_slices_went(fake_slicer, tmp_path, capsys):
    """--keep-slices exists for runs that go wrong, so the path has to survive one."""
    kept = tmp_path / "slices"
    fake_slicer.normal = None
    fake_slicer.returncode = 1
    fake_slicer.stderr = "Error: The supplied file could not be read\n"
    argv = auto_argv(fake_slicer, tmp_path / "out.gcode", extra=["--keep-slices", str(kept)])
    code, stdout, stderr = run(argv, capsys)
    assert code == EXIT_USAGE
    assert f"keeping both slices in {kept}" in stdout
    assert "could not be read" in stderr[0]
