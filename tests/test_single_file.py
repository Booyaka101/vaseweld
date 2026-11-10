"""The standalone vaseweld.py is generated; it must not drift from src/vaseweld/."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import fixture

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_single_file import build  # noqa: E402


def test_standalone_file_is_up_to_date():
    expected = build(ROOT / "src" / "vaseweld")
    actual = (ROOT / "vaseweld.py").read_text(encoding="utf-8")
    assert actual == expected, "run: python tools/build_single_file.py"


def test_standalone_file_produces_identical_output(tmp_path):
    args = [
        "weld",
        "--normal",
        str(fixture("prusaslicer_normal_6mm.gcode")),
        "--vase",
        str(fixture("prusaslicer_vase_6mm.gcode")),
        "--at",
        "3.0",
    ]
    standalone = tmp_path / "standalone.gcode"
    package = tmp_path / "package.gcode"

    first = subprocess.run(
        [sys.executable, str(ROOT / "vaseweld.py"), *args, "-o", str(standalone)],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr

    second = subprocess.run(
        [sys.executable, "-m", "vaseweld.cli", *args, "-o", str(package)],
        capture_output=True,
        text=True,
        cwd=ROOT / "src",
    )
    assert second.returncode == 0, second.stderr

    assert standalone.read_bytes() == package.read_bytes()
    assert first.stdout.replace(str(standalone), "") == second.stdout.replace(str(package), "")


def test_standalone_json_report_matches_the_package():
    args = ["check", str(fixture("prusaslicer_normal_6mm.gcode")), "--json"]
    standalone = subprocess.run([sys.executable, str(ROOT / "vaseweld.py"), *args],
                                capture_output=True, text=True, check=False)
    package = subprocess.run([sys.executable, "-m", "vaseweld.cli", *args],
                             capture_output=True, text=True, cwd=ROOT / "src", check=False)
    assert standalone.returncode == package.returncode == 0
    assert json.loads(standalone.stdout) == json.loads(package.stdout)
