# PROGRESS

vaseweld 1.2.0. Live at https://github.com/Booyaka101/vaseweld with the demo published at
https://booyaka101.github.io/vaseweld/. Not on PyPI and not tagged yet.

A stranger can see it work without installing anything: `vaseweld preview` writes a self-contained
HTML page of the real toolpath, `python sim/demo.py` builds three of them plus an index from a fresh
clone, and `.github/workflows/pages.yml` publishes that to GitHub Pages.

## Verified working

Every claim below was executed on this machine, not inferred.

- **Phase 0 resources**: all seven URLs in the brief re-fetched and confirmed, including the exact
  quotes. PrusaSlicer #3204 is open with 88 reactions and 42 comments, last touched 2026-09-02 by
  the legacy-issue bot. OrcaSlicer #4625 is closed `not_planned` (the repo moved to
  `OrcaSlicer/OrcaSlicer`, so the API URL redirects). Cura #7893 is open with 27 comments.
  BambuStudio #9657 is open. `SpiralVase.cpp` carries the `transition_in` / `transition_out` logic
  and the FIXME about relative extruder distances. The two PrusaSlicer refusal strings were pulled
  out of the shipped `PrusaSlicer.dll` rather than paraphrased.
- **Fixtures are real slicer output.** PrusaSlicer 2.9.6, OrcaSlicer 2.4.2 and BambuStudio
  02.08.02.61 were downloaded as portable builds and driven from the command line. Nothing in
  `tests/fixtures/` was hand-written. `tests/fixtures/README.md` has the exact commands.
- **Three delivery paths, byte-identical output.** Wheel installed into a clean venv, standalone
  `vaseweld.py`, and a PyInstaller `vaseweld.exe` built and run on Windows. All three produced
  sha256 `5c03b42c1bf4ac10...` for the same weld.
- **138 tests pass** with `python -m pytest`, about 30 seconds. That includes a matrix that welds
  all three slicers in both directions at two cut heights and runs `check` on every result.
- **Three slicers, both directions, two cut heights.** All twelve welds pass `vaseweld check`.
- **A real printer firmware accepts the output.** `sim/` builds Klipper for its `linux` MCU target
  in Docker and runs `klippy` in batch mode, which plans every move through the real cartesian
  kinematics and the real extruder limits. Both vaseweld welds plan cleanly in both extruder modes;
  the hand splice people use today is rejected outright with 40 moves over the extrusion limit, the
  worst at 72.5 mm^2 against a 0.64 mm^2 ceiling. `.github/workflows/simulate.yml` runs it on main
  and asserts both halves of that result, so the harness cannot silently stop being sensitive.

## The three real defects the review pass found

All three came from testing against a slicer the first implementation had not seen. Every test was
green against PrusaSlicer alone.

1. **The first move after the seam dragged a line across the print.** A vase layer assumes the
   nozzle is already on its spiral, because in the source file the previous layer ended there. After
   a weld it is wherever the other file stopped, so the first spiral move extruded across 10 mm of
   open air. The seam now travels to the position the slice above expects.
2. **BambuStudio welds came out double-retracted.** PrusaSlicer and OrcaSlicer put the layer-change
   retraction at the start of the next layer; BambuStudio puts it at the end of the previous one and
   splits it between a wiping move and an E-only tail. Adding a fixed retract at the seam left the
   nozzle 0.8 mm under-primed for the rest of the print. The seam now computes the difference
   between the retraction state the file below leaves and the state the file above assumes, and
   emits only that. This also forced `check` off a line-counting retraction model onto a running
   retracted-state model, which is the only one that works across all three slicers.
3. **The retraction correction was inserted in the wrong place.** For a BambuStudio vase-first weld
   it landed after the incoming layer's own `G1 E0.8` unretract instead of before it, so the nozzle
   over-primed by a full retraction, blobbed, and then retracted immediately before printing. Found
   by running `check` on the shipped exe's output rather than trusting the unit tests. The seam now
   distinguishes lines that belong at the boundary from lines that belong just before the first
   printing move, and there is a 12-case matrix test that welds every slicer in both directions at
   two heights and runs `check` on each result.

## Handled in the last pass

- The repository branch was `master` while both workflows trigger on `main` and the README links
  `raw.githubusercontent.com/.../main/vaseweld.py`. CI would never have fired. Renamed to `main`.
- CRLF input now has a test. The behaviour was already right (the newline style is detected and
  reproduced) but nothing proved it.
- `--force` could squeeze a thick layer into a thin gap in silence. Welding a 0.2 mm file to a
  0.3 mm one leaves a 0.2 mm step that the layer above expects to be 0.3 mm, so it lays 150% of the
  material the gap can take. It now says so on stderr.

## What is not done

- **No physical print.** Two simulations stand in for it. Klipper's own host process plans every
  move through the real kinematics and extruder limits, and `sim/deposit.py` models the bead each
  move lays, reproducing the slicer's own 0.450 mm nominal width and showing the weld layer at
  0.425 mm where the ramp starts. Between them they prove the file is executable and that the
  material lands where it should. Neither can prove layer adhesion or surface finish. Only a print
  can.
- **No photo of a physical print in the README.** There is no printer and no camera here. The README
  leads with a labelled render of the real welded toolpath instead, generated by
  `tools/render_preview.py` from the actual output file. The brief asked for a photo; substituting a
  render is the honest option, and it is captioned as a render. **This is the one thing to replace
  before posting anywhere**: print the hybrid, photograph it, drop it in as the lead image.
- **Not on PyPI, not tagged.** The repo is public and all three workflows are green on `main`, but
  `twine upload` and `git tag v1.2.0` have not been run.
- **`pipx run vaseweld` is untested against a real release**, because it resolves from PyPI. The
  local equivalent (clean venv + wheel + console script) passes. Note the uv/pipx trap: the console
  script is named `vaseweld`, the same as the package, so `uvx vaseweld` and `pipx run vaseweld`
  will both work once published.

## Shipping steps for the owner

1. ~~Push local `main` to a public `Booyaka101/vaseweld`.~~ Done, CI green across
   Linux/macOS/Windows on Python 3.10 to 3.13.
2. Print the hybrid, photograph it, replace `docs/weld-preview.png` as the lead image (keep the
   render lower down, it explains the mechanism).
3. `python -m build && python -m twine upload dist/*` for PyPI.
4. `git tag v1.2.0 && git push --tags` fires `.github/workflows/release.yml`, which builds
   `vaseweld.exe`, checks the tag matches `__version__`, and attaches the exe, wheel, sdist and
   standalone `vaseweld.py` to the GitHub release. Cut the tag only after CI is green on that exact
   commit.
5. ~~Enable GitHub Pages.~~ Done, the demo is live at https://booyaka101.github.io/vaseweld/ and rebuilds on every push.
6. Best first distribution step: comment on
   [PrusaSlicer #3204](https://github.com/prusa3d/PrusaSlicer/issues/3204). Six years of `+1`, the
   bot has just labelled it legacy and threatened to auto-close it, and the thread's own regulars
   have twice asked people to stop posting `+1` and post something substantive. A working tool is
   that. A draft comment is ready; it needs the repo and Pages URLs filled in and your go before it
   is posted. The r/3Dprinting and r/prusa3d posts come after, with a print in hand.

## Added in 1.1.0

- **Repeatable `--at`.** Every cut alternates between the two files again, so one run can produce a
  solid base, a vase body and a solid lid. Each seam gets its own travel, retraction match and flow
  ramp. Verified through Klipper (`5_*_vaseweld_two_cuts` plans cleanly) and through the deposition
  model at both seams: 0.425 mm ramping in at layer 62, 0.239 mm ramping out at 149, 0.450 mm again
  at 150.
- **`vaseweld layers FILE`** prints the ladder and the weldable range.

## Next steps, in the order they are worth doing

- **`.bgcode` passthrough** by shelling out to the `bgcode` converter when it is on PATH, instead of
  refusing.
- **Cura.** `parser.py` already recognises Cura's `;LAYER:` markers, but Cura writes no config block,
  so `compat.py` would fall back to the first-layer footprint alone, and there is no Cura fixture.
  Do not claim Cura support until there is one.
- **Sequential (`complete_objects`) plates**, where more than one object is legitimate because they
  print one at a time.
