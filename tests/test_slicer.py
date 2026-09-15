from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import vaseweld.slicer
from conftest import fixture
from vaseweld.slicer import (
    SPIRAL_VASE_OVERRIDES,
    Slicer,
    SlicerError,
    find_slicer,
    probe_slicer,
    run_slice,
    slicer_argv,
    version_complaint,
)

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


def test_an_explicit_directory_without_a_binary_says_which_names_it_wanted(tmp_path):
    with pytest.raises(SlicerError) as excinfo:
        find_slicer(tmp_path)
    assert "no PrusaSlicer binary in it" in str(excinfo.value)
    assert "prusa-slicer-console.exe" in str(excinfo.value)


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
