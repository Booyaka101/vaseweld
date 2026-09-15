# Changelog

## 1.4.0

- **`vaseweld auto` slices the project for you.** Point it at a `.3mf` or any model PrusaSlicer
  opens and it runs both passes itself, normal and spiral vase, then welds them:
  `vaseweld auto vase.3mf --at 6.0 -o hybrid.gcode`. The manual route is unchanged and still works
  with OrcaSlicer and BambuStudio output; `auto` drives PrusaSlicer 2.9.x only, because the other
  two take a different command line and keep their settings in profile JSON rather than flags.
- The spiral pass carries the whole companion override set, not just `--spiral-vase`. PrusaSlicer's
  GUI turns off perimeters, top layers, infill, supports and thin walls alongside the checkbox, from
  a dialog that never runs headless. Measured against 2.9.6, three of those are already forced by
  `normalize_fdm`, two stop `validate()` refusing the slice outright when your profile has supports
  on, and `thin_walls` is the one that quietly moves the toolpath. `--spiral-vase=1 --thin-walls=0`
  reproduces the full set byte for byte; `--spiral-vase=1` on its own does not.
- A plate `auto` cannot weld is refused from the project file, before either slice runs. Two objects,
  two copies of one object, or parts assigned to different extruders all cost a message rather than
  two slicing runs.
- Both passes are compared layer by layer before welding. If the two Z ladders differ at all, `auto`
  names the first layer that disagrees and stops, rather than producing a plausible-looking file that
  does not print. Adaptive layer height causes this, and so do supports. PrusaSlicer will not slice
  spiral vase with support material at all, so the spiral pass never has them and the normal pass
  does, which moves the Zs apart. A profile with supports on is told so before the first pass, and if
  it gets as far as the ladder the abort names supports rather than guessing at layer height.
- Versions other than 2.9.x are called out. 3.0.0-alpha11 refactored the whole command line parser
  upstream, so `auto` refuses to drive a 3.x build unless you pass `--force-slicer-version`. Older
  builds warn and carry on.
- PrusaSlicer exits 0 and writes nothing when a slice fails, so the return code alone proves nothing.
  `auto` checks the file exists and reports the last line PrusaSlicer printed when it does not. An
  `-o` that cannot be written, into a directory that is not there or onto a directory itself, is
  refused before the first pass rather than after both, and so is a project that is a directory
  rather than a file, including `.` and a drive root, which used to reach a Python traceback.
- The normal pass really is a normal pass. Passing `--spiral-vase=0` turns the mode off, but when
  spiral vase arrives through `--load` PrusaSlicer folds it into the config before it looks at the
  command line, so an ini holding `perimeters = 3`, `top_solid_layers = 5`, `fill_density = 20%`
  still sliced its normal pass at `1`, `0`, `0%` with 0 perimeter and 0 infill sections: a hollow
  single-wall base, which is the one thing the weld exists to avoid. Layer-change retraction goes
  the same way at both the printer and the filament level, worth a retraction at every layer change
  on a profile that does not already retract before travel. The filament one is the one that
  matters, because where the two disagree the filament value wins, and an ini exported from
  PrusaSlicer never mentions it: an override nobody set is simply absent. `auto` now reads the
  project's own print settings, layers any `--load` ini over them the way PrusaSlicer does, and
  hands those five values back explicitly, the filament override as `nil` where the profile never
  set it, which reproduces a plain non-vase slice line for line. It hands them back on every run,
  not only when the merged config still says spiral vase is on, because `normalize_fdm` runs as each
  `--load` file is read rather than once at the end: a vase ini followed by an ini holding nothing
  but `spiral_vase = 0` still slices at `1`, `0`, `0%`. When nothing turned the mode on, what goes
  back is what the profile already said, or the slicer's own default for a key it never named, so
  that run gets a longer command line and the same G-code. A `.3mf` project's
  embedded settings turn out not to be clobbered like this, so for a project the overrides hand back
  what the file already said. Where the project was saved after accepting PrusaSlicer's "shall I
  adjust those settings" dialog the originals are gone from the file, and `auto` says so rather
  than pretending otherwise. It says it whenever the settings it is handed are the vase set, not
  only when the flag is still on, because an ini that turns the mode off and changes nothing else
  leaves exactly the same single-wall base. As a backstop it also reads `spiral_vase` back out of
  both sliced files and refuses to weld two passes that came out in the same mode, or the wrong way
  round.
- A plate is counted the way PrusaSlicer counts it. Objects parked as not printable are not on the
  plate, and volume extruder `0` means "inherit the object's extruder" rather than a second
  material, so neither is refused any more. A volume left at `0`, or carrying no extruder key at
  all, next to one assigned to extruder 2 is still two materials and is still refused. Support
  enforcers, blockers, modifiers and negative volumes lay no plastic, so they no longer read as a
  second material on an object printed with extruder 2. Copies are counted as copies: a copy is its
  own object in the model file, aliased to the first one's mesh, so two objects with two copies each
  are reported as two objects and four instances rather than four objects. A plate with an empty
  build section has nothing on it, and is refused as such rather than counted from the objects the
  project file still lists. Reading the plate streams
  the model rather than holding it: a 200 MB mesh cost about 450 MB of memory to find one tag at the
  end of the file, and now costs about 6 MB.
- `--at nan` is refused at the flag, along with `inf`, anything else that is not a number of
  millimetres, and the same for `--slicer-timeout`, which used to reach `subprocess.run` and raise
  with PrusaSlicer already launched. A timeout of zero or less is refused too, rather than starting
  a pass in order to kill it.
- A damaged `.3mf` gets the "not a readable 3MF" message rather than a Python traceback. Damage to
  the deflate stream raises `zlib.error`, which is not `BadZipFile` and was not caught, and damage
  to just the print settings got past the plate check to crash later. The weldable-range check was a pair of one-sided comparisons and nan is false against
  both ends, so it used to get all the way to a crash, after both slicing passes in the case of
  `auto`. The range check itself is a containment test now as well, so nothing gets through it that
  cannot name a layer.
- `--slicer-path` accepts a macOS `.app` bundle, not just the binary buried inside it, and the
  error for a directory with no slicer in it names something that exists on your platform. Where
  several versions are installed side by side, the newest is tried first by version number rather
  than by name, so 2.10.0 will outrank 2.9.6 when it ships.
- `--verbose` prints the exact command line each pass runs, quoted for your shell, before the output
  of that pass. `--keep-slices DIR` keeps both intermediate slices instead of using a temp dir, which
  is what the Z-ladder abort tells you to reach for, unless you already passed it, in which case the
  abort names the directory the two passes are sitting in. Running twice into the same kept directory
  is safe: each pass deletes its target first, so a failed slice can never weld the previous run's
  output.

## 1.3.0

- **Binary G-code (`.bgcode`) can be welded, checked, previewed and listed.** It used to be refused
  with a note telling you which setting to turn off, which is the first thing a Prusa MK4 or Core
  One owner hits, because binary G-code is on by default on those profiles. The decoder is in the
  package: the block structure, heatshrink and MeatPack, about 260 lines and still no dependencies.
  Binarising moves the config block and the filament and time totals out of the G-code stream into
  metadata blocks, so those are put back where a text file would have carried them and the
  compatibility check, the totals and the time remapping all work unchanged. Verified by welding a
  binary pair and a text pair sliced from the same model with the same flags: the two welds carry
  the same commands, line for line.
- The output is still text G-code. `-o something.bgcode` is refused rather than writing a text file
  under a binary name, which is a thing a printer would be entitled to reject.

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
