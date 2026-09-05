"""Run the whole thing end to end and leave a folder you can show someone.

Uses only the committed fixtures and the standard library, so it works on a fresh
clone with nothing installed:

    python sim/demo.py

It writes the welded G-code, checks it, and builds the interactive previews. The
Klipper simulation is a separate step because it needs Docker; see sim/README.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vaseweld import __version__  # noqa: E402
from vaseweld.parser import parse_file  # noqa: E402
from vaseweld.preview import write as write_preview  # noqa: E402
from vaseweld.validate import check  # noqa: E402
from vaseweld.weld import weld  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

CASES = [
    (
        "vase-above",
        "a solid base with the vase starting at Z=12.4",
        [12.4],
        "normal",
    ),
    (
        "vase-below",
        "a vase body with a solid lid from Z=25",
        [25.0],
        "vase",
    ),
    (
        "base-vase-lid",
        "a solid base, a vase body from Z=12.4, and a solid lid from Z=30",
        [12.4, 30.0],
        "normal",
    ),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "docs" / "demo")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"vaseweld {__version__}: welding the committed fixtures\n")
    normal = parse_file(FIXTURES / "prusaslicer_normal_40mm.gcode")
    vase = parse_file(FIXTURES / "prusaslicer_vase_40mm.gcode")
    print(
        f"  normal slice: {len(normal.layers)} layers, Z {normal.layers[0].z} to {normal.layers[-1].z}"
    )
    print(
        f"  vase slice:   {len(vase.layers)} layers, Z {vase.layers[0].z} to {vase.layers[-1].z}\n"
    )

    written = []
    for name, blurb, cuts, first_role in CASES:
        print(f"{name}: {blurb}")
        result = weld(normal, vase, cuts, first_role=first_role)
        for line in result.summary():
            print(f"    {line}")
        gcode = args.out / f"{name}.gcode"
        gcode.write_text(
            result.newline.join(result.lines) + result.newline, encoding="utf-8", newline=""
        )
        report = check(gcode)
        print(f"    {report.summary()}")
        for problem in report.problems:
            print(f"    {problem}")
        page = write_preview(args.out / f"{name}.html", gcode)
        print(f"    preview: {page.name} ({page.stat().st_size / 1024:.0f} KB)\n")
        written.append((name, blurb, page.name, gcode.name, report.ok))

    (args.out / "index.html").write_text(_index(written), encoding="utf-8")
    print(f"open {args.out / 'index.html'}")


def _index(cases: list[tuple[str, str, str, str, bool]]) -> str:
    cards = "".join(
        f'<a class="card" href="{page}"><h2>{name}</h2><p>{blurb}</p>'
        f'<p class="ok">{"vaseweld check: OK" if ok else "vaseweld check: FAILED"}</p>'
        f'<p class="raw">G-code: {gcode}</p></a>'
        for name, blurb, page, gcode, ok in cases
    )
    return _INDEX.replace("__CARDS__", cards).replace("__VERSION__", __version__)


_INDEX = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vaseweld demo</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0 auto; padding: 32px 20px; max-width: 780px;
         font: 15px/1.6 system-ui, sans-serif; background: #fafaf9; color: #3c3c41; }
  h1 { font-size: 21px; margin: 0 0 4px; }
  .sub { color: #85858c; margin: 0 0 26px; }
  .card { display: block; text-decoration: none; color: inherit; border: 1px solid #e6e6e3;
          border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; background: #fff; }
  .card:hover { border-color: #c9c9c4; }
  h2 { font-size: 16px; margin: 0 0 4px; }
  p { margin: 0; }
  .ok { color: #3f8f4f; font-size: 13px; margin-top: 6px; }
  .raw { color: #9a9aa0; font-size: 12px; }
  code { background: #f0f0ec; padding: 1px 5px; border-radius: 4px; font-size: 13px; }
  @media (prefers-color-scheme: dark) {
    body { background: #17171a; color: #dcdce0; }
    .card { background: #1f1f23; border-color: #34343a; }
    .card:hover { border-color: #4a4a52; }
    code { background: #2a2a30; }
  }
</style>
<h1>vaseweld __VERSION__</h1>
<p class="sub">Three welds of the same pair of slices, each drawn from its real G-code.
Open one and drag the slider past a weld.</p>
__CARDS__
<p class="sub" style="margin-top:22px">Rebuild all of this from a fresh clone with
<code>python sim/demo.py</code>. Nothing to install.</p>
</html>
"""


if __name__ == "__main__":
    main()
