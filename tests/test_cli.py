from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from conftest import fixture, weld_argv
from vaseweld import __version__
from vaseweld.cli import EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, main

ROOT = Path(__file__).resolve().parent.parent


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err.splitlines()


def test_weld_then_check(tmp_path, capsys):
    out = tmp_path / "out.gcode"
    code, stdout, _ = run(
        weld_argv(
            normal="prusaslicer_normal_40mm.gcode",
            vase="prusaslicer_vase_40mm.gcode",
            at="12.4",
            output=out,
        ),
        capsys,
    )
    assert code == EXIT_OK
    assert stdout[:5] == [
        "cut snapped to Z=12.400 (layer 62)",
        "normal: layers 1-61 (Z 0.200-12.200)",
        "vase: layers 62-200 (Z 12.400-40.000)",
        "E mode: absolute -> relative (converted)",
        "transition ramp: 0.80 -> 1.00 over layer 62",
    ]
    assert stdout[5] == f"wrote {out} ({len(out.read_text(encoding='utf-8').splitlines())} lines)"

    code, stdout, _ = run(["check", str(out)], capsys)
    assert code == EXIT_OK
    assert stdout == [
        "OK: Z monotonic, E coherent (relative), retractions balanced, 1 temperature timeline"
    ]


def test_vase_first_inverts_the_order(tmp_path, capsys):
    out = tmp_path / "out.gcode"
    code, stdout, _ = run(weld_argv(output=out, extra=["--vase-first"]), capsys)
    assert code == EXIT_OK
    assert stdout[1].startswith("vase: layers 1-14")
    assert stdout[2].startswith("normal: layers 15-30")
    assert stdout[4].startswith("transition ramp: 1.00 -> 0.25")


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        pytest.param(
            {"vase": "mismatch_layerheight_6mm.gcode"}, "'layer_height'", id="profile-mismatch"
        ),
        pytest.param({"normal": "binary_6mm.bgcode"}, "binary G-code", id="binary-gcode"),
        pytest.param({"normal": "two_objects_1mm.gcode"}, "single object", id="two-objects"),
        pytest.param({"at": "99"}, "Valid range is Z 0.400 to 6.000", id="cut-too-high"),
        pytest.param({"at": "0.2"}, "Valid range is Z 0.400 to 6.000", id="cut-on-layer-one"),
        pytest.param({"extra": ["--start-flow", "1.5"]}, "between 0 and 1", id="start-flow-high"),
        pytest.param({"extra": ["--finish-flow", "-0.1"]}, "between 0 and 1", id="finish-flow-low"),
        pytest.param({"vase": None}, "need both slices", id="one-slice-only"),
    ],
)
def test_weld_refusals_name_the_problem(tmp_path, capsys, kwargs, expected):
    out = tmp_path / "out.gcode"
    code, _, stderr = run(weld_argv(**{"output": out, **kwargs}), capsys)
    assert code == EXIT_USAGE
    assert expected in stderr[0]
    assert not out.exists()


def test_no_destination_is_refused(capsys):
    code, _, stderr = run(weld_argv(), capsys)
    assert code == EXIT_USAGE
    assert "no destination" in stderr[0]


def test_missing_input_file(tmp_path, capsys):
    code, _, stderr = run(
        weld_argv(normal=tmp_path / "nope.gcode", output=tmp_path / "out.gcode"), capsys
    )
    assert code == EXIT_USAGE
    assert "no such file" in stderr[0]


def test_force_downgrades_the_refusal_to_a_warning(tmp_path, capsys):
    out = tmp_path / "out.gcode"
    code, _, stderr = run(
        weld_argv(vase="mismatch_layerheight_6mm.gcode", output=out, extra=["--force"]), capsys
    )
    assert code == EXIT_OK
    assert any("layer_height" in line for line in stderr)
    assert out.exists()


def test_stripped_stats_are_reported_on_stderr(tmp_path, capsys):
    code, _, stderr = run(weld_argv(output=tmp_path / "out.gcode"), capsys)
    assert code == EXIT_OK
    assert stderr == [
        "note: removed the printing time estimate, which cannot be recomputed from these two files"
    ]


def test_check_reports_problems_and_exits_one(tmp_path, capsys):
    broken = tmp_path / "broken.gcode"
    lines = fixture("prusaslicer_normal_6mm.gcode").read_text(encoding="utf-8").splitlines()
    lines.insert(len(lines) // 2, "M109 S215")
    broken.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code, stdout, _ = run(["check", str(broken)], capsys)
    assert code == EXIT_PROBLEMS
    assert stdout[0].startswith("FAIL: 1 problem in broken.gcode")
    assert "M109 S215" in stdout[1]


def test_version_matches_the_package(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"vaseweld {__version__}"


def test_module_entry_point_runs():
    # Run from src/ so the generated vaseweld.py at the repo root cannot shadow
    # the package of the same name.
    result = subprocess.run(
        [sys.executable, "-m", "vaseweld.cli", "--version"],
        capture_output=True,
        text=True,
        cwd=ROOT / "src",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"vaseweld {__version__}"


def test_dry_run_reports_the_plan_without_writing(tmp_path, capsys):
    out = tmp_path / "out.gcode"
    code, stdout, _ = run(weld_argv(output=out, extra=["--dry-run"]), capsys)
    assert code == EXIT_OK
    assert stdout[0] == "cut snapped to Z=3.000 (layer 15)"
    assert stdout[-1].startswith(f"would write {out} (")
    assert not out.exists()


def test_force_reports_the_seam_step_it_created(tmp_path, capsys):
    code, _, stderr = run(
        weld_argv(
            vase="mismatch_layerheight_6mm.gcode",
            output=tmp_path / "out.gcode",
            extra=["--force"],
        ),
        capsys,
    )
    assert code == EXIT_OK
    assert any("0.200 mm step" in line and "150%" in line for line in stderr)
