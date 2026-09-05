# Changelog

## 1.2.2

- The demo printed a cylinder. Every fixture in this repository is a plain 20 mm tube, which is the
  right shape for a test (constant radius, so a bug in the weld has nowhere to hide) and the wrong
  one for showing someone a tool called vaseweld: the top view was a disc and the front view a
  rectangle, whichever section came from the vase slice. `examples/vase_40mm.stl` is a real vase
  profile now, with a belly, a neck and a flared lip, and the demo and the README lead image are
  built from slices of it. The test fixtures are untouched.
- `tools/make_cylinder_stl.py` is now `tools/make_stl.py --shape {cylinder,vase}`, one surface of
  revolution generator instead of two. The cylinders it writes are the same solid as the committed
  ones, triangle for triangle.

## 1.2.1

- The preview was broken on phones and on any HiDPI screen. It had no viewport meta tag, so phones
  laid it out at 980 px and zoomed out, and the canvases had no CSS width, so setting
  `canvas.width` for the device pixel ratio also changed the element's layout width and blew it up
  to 1560 px. Both are fixed: the canvas is sized by CSS and the backing store follows it.
- On a narrow screen the two views and the slider now fit one screen, with the controls pinned to
  the bottom. Before this the front view drew its content below the fold.
- The slider starts at the first weld rather than the last layer, which is the thing worth looking
  at rather than a finished lid.
- Redraws are throttled to one per frame, so dragging the slider on a phone does not queue work.

## 1.2.0

- **`vaseweld preview FILE`** writes one self-contained HTML page: the whole toolpath, every bead
  drawn at the width the G-code asks for, coloured by which slice it came from. Open it in any
  browser and drag the slider past a weld to watch stacked layers become a spiral. No server, no
  dependencies, nothing to install. It is how you show someone the tool works without a printer.
- **`python sim/demo.py`** welds the committed fixtures three ways, checks each result and builds
  the previews plus an index page, from a fresh clone with nothing installed.
- `.github/workflows/pages.yml` publishes that demo to GitHub Pages.
- Previewing is a single pass over the file rather than re-deriving the extruder state per layer,
  which took a 34k-line file from about 15 seconds to 0.16.

## 1.1.0

- **`--at` can be repeated.** Every cut alternates between the two files again, so
  `--at 12.4 --at 30` gives a solid base, a vase body and a solid lid in one run. Each seam gets its
  own travel, retraction match and flow ramp: the spiral ramps up where it starts and back down
  where it ends. This is the variant the slicer issues ask for most often after the feature itself.
- **`vaseweld layers FILE`** prints the Z ladder, the layer height and the weldable range, so you
  can pick a cut without guessing and re-running. `--all` lists every layer.
- A vase section only one layer tall ramps flow up but not back down, and says so.
- `weld()` now takes `(normal, vase, cut_z, first_role=...)` where `cut_z` is a height or a list of
  them. The old `bottom`/`top` plus `bottom_role`/`top_role` form is gone; the command line is
  unchanged apart from `--at` accepting repeats.

## 1.0.0

First release.

- `vaseweld weld` splices a normal slice and a spiral vase slice at a chosen Z height. Default is
  normal below and vase above; `--vase-first` inverts it.
- `--dry-run` reports the plan without writing, so you can confirm the snap first.
- `vaseweld check` verifies Z monotonicity, extrusion coherence, retraction balance and the
  temperature timeline on any G-code file.
- Output is always relative E. Absolute-E inputs are converted by differencing consecutive values,
  with `G92 E0` at the seam.
- Flow across the transition layer is ramped from `spiral_starting_flow_ratio` to 1.0, or from 1.0
  down to `spiral_finishing_flow_ratio`, matching what the slicer does at its own vase transition.
  Fallbacks are 0.8 and 0.25, overridable with `--start-flow` and `--finish-flow`.
- The seam travels to where the slice above expects the nozzle and matches the retraction state it
  assumes, so the first move after the weld neither drags a line nor starts under-primed. Slicers
  disagree about who owns the layer-change retraction, so this correction differs per slicer.
- Refuses to weld files that disagree on `layer_height`, `first_layer_height`, `nozzle_diameter`,
  `filament_diameter`, `bed_shape`, `printer_model`, object instance count or object placement, and
  names the field. `--force` downgrades this to a warning, and then reports what the
  mismatch does to the seam, such as a thick layer landing in a thin gap.
- Refuses multi-object and multi-extruder files, quoting PrusaSlicer's own validator.
- Refuses binary `.bgcode` with the setting to change.
- Understands three G-code layouts: PrusaSlicer and OrcaSlicer with a trailing config block and
  `;LAYER_CHANGE` / `;Z:` markers, and BambuStudio with a leading config block and
  `; CHANGE_LAYER` / `; Z_HEIGHT:` markers.
- Rewrites Klipper `SET_PRINT_STATS_INFO` counters, the total-layer comments and BambuStudio's
  `HEADER_BLOCK` totals, recomputes the
  filament totals, and remaps `M73` progress and the time estimate when the inputs carry remaining
  times. Strips the time comments when they cannot be recomputed.
- Runs as a slicer post-processing script: leave out the side the slicer is producing and it takes
  the trailing temp file, rewriting it in place. Reports `SLIC3R_PP_OUTPUT_NAME` when set.
- `sim/` runs every case through Klipper's own motion planner in Docker, including the hand splice
  vaseweld replaces, which the firmware rejects. `sim/deposit.py` then models the bead each move
  lays and draws the weld layer, which is where the hand splice's dragged thread is visible.
- Ships three ways from one source: a PyPI package with a `vaseweld` console script, a standalone
  `vaseweld.py` generated by `tools/build_single_file.py`, and a PyInstaller `vaseweld.exe`.
