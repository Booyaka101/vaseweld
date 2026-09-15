# PROGRESS

vaseweld 1.4.0 is built and green locally, not published. 1.3.0 is the live release:
`pip install vaseweld`, or
[the release](https://github.com/Booyaka101/vaseweld/releases/tag/v1.3.0) for the exe and the
standalone script. Demo at https://booyaka101.github.io/vaseweld/. Announced on
[PrusaSlicer #3204](https://github.com/prusa3d/PrusaSlicer/issues/3204#issuecomment-5551899954).

A stranger can see it work without installing anything: `vaseweld preview` writes a self-contained
HTML page of the real toolpath, `python sim/demo.py` builds three of them plus an index from a fresh
clone, and `.github/workflows/pages.yml` publishes that to GitHub Pages.

## 1.4.0: `vaseweld auto`

`vaseweld auto project.3mf --at 6.0 -o hybrid.gcode` runs both slicing passes itself and welds them,
so nobody has to slice the same plate twice by hand. It drives PrusaSlicer 2.9.x only.

Everything below was executed on this machine against a real PrusaSlicer 2.9.6, not inferred.

- **The brief's mechanism claim was wrong for 2.9.6, and the argv it specified is still right.** The
  brief said the six companion keys the GUI toggles alongside spiral vase are all needed because
  libslic3r does not apply them headless. Measured:
  - `DynamicPrintConfig::normalize_fdm` (`PrintConfig.cpp:5523-5537`) already forces `perimeters=1`,
    `top_solid_layers=0` and `fill_density=0` whenever `spiral_vase` is on. Those three overrides are
    redundant.
  - `PrintConfig::validate` (`PrintConfig.cpp:5868-5881`) hard-rejects spiral vase together with
    `support_material` or `support_material_enforce_layers>0`:
    "Error: The composite configation is not valid: Spiral vase mode is not compatible with support
    material", exit 1, no file written. Those two overrides turn a hard failure into a working slice
    for anyone whose profile has supports on.
  - `thin_walls` is the only key nothing upstream corrects. `--spiral-vase=1 --thin-walls=0`
    reproduces the full companion set byte for byte; `--spiral-vase=1` alone differs by 16 lines and
    586 bytes.
  All seven flags are still passed, for the reasons above rather than the reason the brief gave.
- **PrusaSlicer 2.9.6 has no `--version`.** It exits 1 and prints the banner on stdout. `--help` is
  the only way to read the version, which is what `probe_slicer` does.
- **PrusaSlicer exits 0 on a failed slice and writes nothing.** "All objects are outside of the
  print volume" is exit 0. The return code proves nothing, so `run_slice` checks the output file
  exists and reports the last non-progress line PrusaSlicer printed when it does not.
- **PrusaSlicer's CLI `--export-3mf` never writes `Metadata/Slic3r_PE.config`**, not even with
  `--load`. A project exported that way carries the mesh and its bed position and no print settings,
  and slices at the built-in 0.3 mm default. That is why `examples/vase.3mf` slices at 0.3 mm, and
  it is documented in both `examples/README.md` and `tests/fixtures/README.md` rather than hidden.
- **The worked example runs.** `vaseweld auto examples/vase.3mf --at 6.0 -o vase-hybrid.gcode`
  resolved PrusaSlicer off `PATH`, ran both passes, found 133 layers at 0.300 mm from Z 0.350 to
  39.950, snapped the cut down to Z=5.750 (layer 19) and wrote 21294 lines. `vaseweld check` on the
  result is OK.
- **With no PrusaSlicer it exits 2** with exactly
  `PrusaSlicer not found. Pass --slicer-path, or install it from https://www.prusa3d.com/prusaslicer/`.
  Confirmed on this machine, where the only PrusaSlicer is a portable build outside every standard
  location.
- **258 tests, 257 pass and one is skipped** unless `VASEWELD_E2E=1`. That one drives the real
  binary end to end; it passes here with `VASEWELD_SLICER` pointed at the portable build. 161 of the
  258 predate this release and still pass unchanged.
- **The published artefact was run, not just built.** `python -m build --wheel`, installed into a
  fresh venv, and `vaseweld auto examples/vase.3mf --at 6.0` run through the console entry point
  produced the same 21294-line file, which `vaseweld check` passes.
- **The shared-code extraction changed nothing.** `_add_weld_options`, `_reject_bgcode_output`,
  `_weld_files`, `_ladder` and `_ladder_report` were pulled out of `_run_weld` and `_run_layers` so
  `auto` reuses them instead of cloning them. `tools/baseline.py` records `weld`, `check` and
  `layers` over the whole fixture matrix, 97 files: 25 welded G-code files and 72 CLI transcripts.
  Before and after the extraction they are identical byte for byte. Re-recorded after the version
  bump, the 25 welded files differ by exactly one line each, the `; vaseweld 1.4.0` provenance
  comment, and all 72 transcripts still match.
- **A review pass found five bugs the suite was green through, and each now has a test that fails
  without its fix.** The two that mattered:
  - The normal pass sent no `--spiral-vase` at all, so it inherited whatever the project or the
    `--load` ini said. Reproduced against the real 2.9.6: with `spiral_vase = 1` in the ini the
    "normal" slice came out carrying `; spiral_vase = 1` and three solid sections, which welds into
    a file whose solid base is a single wall. Both Z ladders match, so nothing downstream catches
    it. With `--spiral-vase=0` the same run gives `; spiral_vase = 0` and 55 solid sections.
  - `run_slice` treated an existing output file as proof the pass worked. Two runs of
    `--keep-slices` into the same directory, with the second failing, welded the first run's slices
    and reported success. Each pass now deletes its target before launching.
  The other three: volume extruder `0` means "inherit the default" and was being counted as a
  second material; objects parked as not printable were counted as being on the plate, both of
  which refused plates PrusaSlicer would happily slice; and a future 2.10.x would have been called
  "older than the 2.9.x".
- **A second review pass found four more, including one that made the first pass's headline fix
  half a fix.** Same rule: each has a test proved to fail without it, by reverting that one change
  and watching only its own tests go red.
  - `--spiral-vase=0` turns the mode off but does not always undo it. PrusaSlicer runs
    `normalize_fdm()` over the loaded config before the command line overrides land, so the keys it
    forces stay forced. The measurement first recorded here was wrong and the third pass below
    corrects it: a `.3mf`'s embedded settings survive, it is a `--load` ini that loses them.
    Measured on 2.9.6 through `--load`, an ini holding `perimeters = 3`, `top_solid_layers = 5`,
    `fill_density = 20%` sliced its normal pass at `1`, `0`, `0%`, giving 0 `;TYPE:Perimeter` and
    0 `;TYPE:Internal infill` sections. Argument order makes no difference; passing the values back
    explicitly does, and reproduces a plain non-vase slice section for section. `auto` now reads
    the project's embedded `Metadata/Slic3r_PE.config` and any `--load` ini over it, and hands them
    back. The same run now slices its normal pass with 199 perimeter and 260 internal infill
    sections. Where the values in the file *are* the vase set, nothing can recover them and `auto`
    says so instead of pretending.
  - Dropping extruder `0` outright let a genuinely two-material plate through: one volume at `0`
    beside one at `2` read as a single material. `0` means "inherit the object's extruder", so it
    now resolves through the object-level value rather than being discarded.
  - The Z-ladder abort always said "re-run with `--keep-slices`", and the line naming the directory
    sat below the `raise`. Someone who had already passed it was told to do it again and never told
    where to look.
  - `--slicer-path` at a macOS `.app` was rejected, and the error suggested two Windows `.exe`
    names on every platform.
- **A third review pass found five more.** Same rule again: revert the one change, watch only its
  own tests go red.
  - `normalize_fdm` eats layer-change retraction too, so the three settings handed back were three
    of four. The numbers in the finding did not reproduce, and the real ones are narrower: on a
    profile that retracts at a layer change anyway the key changes nothing, 200 retractions either
    way and 0 differing lines over two models. Set `retract_before_travel = 100` so the profile
    does not already retract and the normal pass drops from 200 retractions to 2, with 909 lines
    different. Worth the fourth slot, but not for the reason reported.
  - The second pass's headline measurement was overstated, and this pass caught it. A `.3mf`
    project's embedded config is not clobbered by `normalize_fdm` at all: with `--spiral-vase=0`
    alone the same kind of project slices its normal pass at `perimeters = 7`, the value it
    carries. Only the `--load` path loses the settings. The code stands, because handing them back
    is a no-op when the file already said them, but the README, the CHANGELOG and the comment now
    say what actually happens rather than what I assumed generalised.
  - One regex read both a project's `; key = value` block and a `--load` ini. In an ini `;` starts
    a comment, so a commented-out `;fill_density = 0%` was read as a live setting and handed back
    on the command line, which is worse than not reading the file at all. Two regexes now, one per
    format.
  - `merged_config` opened the source looking for print settings even when it was an STL, decoding
    megabytes of geometry as text to find nothing. Only a `.3mf` carries settings, so only a `.3mf`
    is opened.
  - `--keep-slices` printed the directory after both passes had finished, so the run that most
    needs the path, one where a pass failed, never printed it. It is printed before the first pass
    now.
- **A fourth review pass found six.** Four were fixed here, each with a test that fails without its
  fix. One did not survive being measured. The sixth I rejected wrongly, and round five caught it.
  - `normalize_fdm` clobbers `filament_retract_layer_change` as well, and that nullable filament
    override beats `retract_layer_change` wherever it is set, so handing the printer key back did
    not always restore the retraction. Measured on 2.9.6 with an ini setting both: through the vase
    ini the normal pass came out with the filament key at 0 against 1 for a plain slice, 398 lines
    shorter, a retraction and its unretract missing at every one of 199 layer changes. Re-run with
    every key normalize is suspected of touching set to something non-default, the two footers now
    differ in nothing at all, and the restored pass matches a plain slice line for line. This pass
    handed the key back only when the config named it, on the belief that a nullable option has no
    command line spelling for "unset". It has one, and the sixth pass below corrects it.
  - `_extruders_used` only counted volumes that carry an `extruder` key, and PrusaSlicer writes one
    only for a volume someone assigned by hand. An object at extruder 2 with one volume pinned to 1
    and one left alone read as a single material, so a genuinely two-material plate cost two full
    slices before `check_compatible` refused it.
  - Reading the plate decoded the whole of `3D/3dmodel.model` to find the `<build>` tag at the end
    of it. Measured: a 208 MB model costs 458 MB of memory that way. It streams the member now and
    stops at `</build>`, same answer in the same 0.3 seconds for 6.4 MB.
  - Install directories and AppImages were sorted by name, so 2.9.6 will outrank 2.10.0 the day it
    ships. They sort by version number now.
  - Not reproduced: that a 3MF's embedded config is applied after `--load`, so the project would
    win and a vase project plus a normal ini would still slice a single wall. It does not win. An
    ini holding 3/5/20% over a project holding 7/4/35% slices at 3/5/20%, key by key, which is what
    `merged_config` already models.
  - Rejected wrongly: that two objects with two copies each are reported as "4 objects". The file I
    built to disprove it gave its four build items objectids 1, 1, 2, 2, which is not how
    PrusaSlicer writes a copy. A copy is its own `<object>` in the model, aliased to the first
    one's mesh through `<components>`, with its own id and its own build item, while the config
    still lists the object once. So the ids really are distinct and the count really was wrong.
    Fixed in the fifth pass, against a file built the way `two_objects.3mf` is.
  - Fell out of measuring the first of those: PrusaSlicer ignores the first line of a project's
    `Metadata/Slic3r_PE.config`, which in a real project is the generator comment. A hand-built
    project that puts a setting on line one loses it silently. `project_3mf` already writes the
    header, so only the throwaway file used for the measurement was wrong, and the fixture now says
    why the line is there.
- **A fifth review pass found three, all of them real.** Two are the counting bugs the fourth pass
  got wrong or missed, and the third is a warning that stayed quiet in the case that needed it.
  - Two objects with two copies each read as four objects. `two_objects.3mf` settles how PrusaSlicer
    writes a copy: alias `<object>`s with their own ids pointing at the first one's mesh through
    `<components>`, one build item each, and the config still listing the object once. So the ids
    the config names are the objects and the rest are copies. A pair of pairs now reports
    "2 objects, 4 instances" and is refused as "a plate with 2 objects", where before it claimed
    four objects that are not there.
  - A support enforcer counted as a second material. Modifiers, blockers, enforcers and negative
    volumes carry no `extruder` key, and the fourth pass had just taught `_extruders_used` to give a
    keyless volume the object's extruder. An object printed with extruder 2 and carrying one
    enforcer read as extruders 1 and 2 and was refused outright. Only `ModelPart` volumes count now.
  - `unrecoverable_vase` was gated on `spiral_vase` being on, so the one case that most needs it
    said nothing: a project saved as a vase plus a `--load` ini holding only `spiral_vase = 0`.
    Measured on 2.9.6, that pair slices its normal pass at `perimeters = 1` with 0 perimeter and 0
    infill sections, and `auto` printed no warning at all. The flag is off and the settings are
    still the vase set, which is exactly the thing worth saying, so the check now looks at the
    settings rather than the flag and the wording follows.
- **A sixth review pass found three, all of them real, and the first is a hole in the fourth
  pass's own fix.**
  - `filament_retract_layer_change` went back only when the config named it, and an ini exported
    from PrusaSlicer does not name it: a nullable filament override that is unset is simply absent.
    So the common case left normalize's `0` standing, and `0` at the filament level beats the
    restored `--retract-layer-change=1`. `nil` is the command line spelling for unset, 2.9.6 takes
    it, and the belief that it had none was wrong. Measured on `cylinder_40mm` with an ini holding
    `retract_layer_change = 1` and nothing about the filament: the normal pass differed from a
    plain slice of the same profile by 405 lines, a retraction and its unretract at all 134 layer
    changes. With `nil` handed back the two toolpaths are identical, nothing to diff. Confirmed
    again through `auto` itself: `--keep-slices` shows the normal pass now matching the plain slice
    line for line, and the footer carries `filament_retract_layer_change = nil`. The key-by-key
    matrix that settles the mechanism is worth keeping: printer 1 with filament unset retracts,
    printer 0 does not, printer 1 with filament 0 does not, printer 0 with filament 1 does. The
    filament value wins whichever way it points. A profile whose travel threshold is 0 retracts at
    every travel anyway and shows none of this, which is what made the first attempt to measure it
    come out blank.
  - `_mode_divergence` had one branch for "both on" and used the other for everything else, so two
    passes that came out the wrong way round were reported as "both passes were sliced with spiral
    vase off (spiral_vase=1 and 0)", which contradicts its own numbers. That case now says the
    normal pass came out as a vase and the spiral vase pass came out normal.
  - `-o` into a directory that does not exist ran both passes in full and then failed to write,
    which is the waste `preflight.py` exists to prevent. The destination is checked before the
    first pass now, after the `--keep-slices` directory has been created, because `-o` inside the
    directory you are keeping the slices in is a reasonable thing to ask for and refusing it would
    have been a worse bug than the one being fixed.
- **A seventh review pass found two, both real, and the second is the worst bug of the loop.**
  - `auto` never said anything about supports until both passes had already run. The spiral pass
    cannot carry them, because `validate()` refuses spiral vase with support material at all, so it
    is always sliced with `--support-material=0`. The normal pass keeps them, supports move the
    layer Zs, and the two ladders then disagree. Measured on three support configurations, default
    contact distance, `support_material_synchronize_layers = 1` and
    `support_material_contact_distance = 0`: all three diverge. So two full slices bought an abort
    whose message blamed adaptive layer height, which was not what happened. The profile is checked
    before the first pass now and says what the spiral pass loses, and if it gets as far as the
    ladder the abort names supports rather than adaptive layers.
  - Handing the vase settings back was gated on the merged config saying spiral vase is on, and
    `normalize_fdm` does not run once over the merged config. It runs as each `--load` file is read.
    Measured on 2.9.6: `--load vase.ini --load off.ini`, where the second file holds nothing but
    `spiral_vase = 0`, still slices at `perimeters = 1`, `top_solid_layers = 0`, `fill_density = 0%`
    and both retraction keys at 0, a 41325-line body difference from a plain slice of the same
    profile, while the merged config says the mode is off and the gate kept quiet. The gate is gone
    and the settings go back every time. Two measurements say that is safe rather than merely
    convenient: slicing with no config at all and slicing with the five defaults passed explicitly
    give a 0-line body diff, so the fallbacks are 2.9.6's own built-ins; and the CLI does not
    resolve `inherits`, so a key a `--load` ini never names really does resolve to the built-in
    default and not to some parent preset's value. An ini holding only
    `inherits = Original Prusa i3 MK2` slices at `retract_layer_change = 0` with an empty
    `printer_model`, the parent ignored outright. Confirmed end to end on a plain profile that sets
    `retract_layer_change = 1`: the normal pass now carries six overrides instead of one, keeps the
    profile's own `1`, and its body matches a plain slice of the same file line for line.
- **An eighth review pass found two, and one of them predates this branch.**
  - `vaseweld auto .` died with a Python traceback instead of "`.` is a directory, not a model".
    The default output name is built from the project's stem, and `Path(".").with_name(...)` raises
    `ValueError: has an empty name`. The same for `..`, an empty string and a drive root. Preflight
    already had the right message for a directory with a name, so it runs before the name is built
    now, not after.
  - `--at nan` ran both slicing passes to completion and then crashed on
    `IndexError: list index out of range`. The range check was a pair of one-sided comparisons,
    `cut_z < lowest or cut_z > highest`, and nan is False against both, so it sailed through and
    `_snap` found no layer at or below it. The hole is in `weld.py` and is as old as `weld`, but
    `auto` is what makes it cost two slicing runs. Both ends are fixed: the check is a containment
    test, which nan fails the way it should, and `--at` refuses anything that is not a finite number
    at the flag, so nothing runs at all. `--at inf` used to reach the range message after slicing
    and is now refused with the rest.
- **Clone check.** difflib over the line lists of every new function against all 112 functions in
  the package. The first pass put `probe_slicer` against `run_slice` at 38.2%: both built the same
  six-keyword `subprocess.run` call, and the review pass had just added `stdin=DEVNULL` to each of
  them by hand, which is exactly the drift the rule is there to catch. The call moved into
  `_capture` and each caller kept its own timeout message. The highest similarity anywhere is now
  22.2%, `slicer_argv` against `run_slice`, which share only the argv they pass. Nothing else
  clears 21%. Re-run over the second pass's new functions: the highest is `unrecoverable_vase`
  against `_mode_divergence` at 26.1%. They both phrase a complaint about spiral vase and share
  nothing else, one reading the config before slicing and one reading the G-code after, so they
  stay apart. `multi_material_3mf` took an `extruders` argument rather than growing a near-copy for
  the `(0, 2)` case, and `_replace_member` learned to add a missing member rather than gaining a
  sibling that only appends. The third pass added no functions at all, only changed existing ones,
  so there was nothing new to diff. The fourth added three, `_newest_first`, `_read_build` and
  `_extruder`, against 153 functions in the package: the highest is 13.3% and the two short ones
  score 0% against everything, sharing not one line with anything else. The fifth added one,
  `_is_part`, and the sixth one, `_reject_unwritable_output`, both scoring 0% against all 434
  functions in the repository including the guard each sits next to. Their new tests peak at 46%
  against the test they are modelled on, which is two assertions and a fixture edit in common. The
  seventh added `supports_are_on`, `dropped_supports` and `_ladder_cause`, topping out at 24.0%,
  `dropped_supports` against `unrecoverable_vase`: both return a sentence or `None` after reading
  the config, and say entirely different things about entirely different keys. The truthiness rule
  they share is real duplication, so it came out into `_is_on` rather than being written twice.
  Removing the spiral vase gate left `_vase_is_on` with no callers and it is gone; ruff does not
  flag a private module function nobody calls, so that one is on the reader. The eighth added one,
  `_cut_height`, at 30.0% against `_retract_length` in `validate.py`, which is the shape of a
  try/except round a `float()` and nothing else. Its two fixes needed no new tests of their own
  either: the nan and inf cases went into the parametrize list of the range test that was already
  there, rather than a near-copy of it.

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
- **The suite passes** with `python -m pytest`, 258 tests in about 45 seconds. That includes a
  matrix that welds all three slicers in both directions at two cut heights and runs `check` on
  every result.
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

- **Binary G-code is read now, not refused.** PrusaSlicer turns it on by default for the MK4 and
  the Core One, so "turn off Supports binary G-code and re-slice" was the first thing a large share
  of the target users would have hit. The decoder is in the package rather than shelled out to the
  `bgcode` converter, which keeps the zero-dependency promise and works on a machine that has never
  heard of libbgcode. Checked three ways: the decoded fixture carries the same commands as its text
  twin line for line, a weld of the binary pair carries the same commands as the weld of the text
  pair, and the 2.3 MB vase decodes in 0.51 s. The first heatshrink loop took 5.48 s for the same
  file; inlining the bit reader and copying non-overlapping matches by slice fixed that.

- **The demo printed a cylinder.** Every fixture here is a 20 mm tube, measured at 19.6 mm wide at
  layers 6, 61, 121 and 200, so the top view was a plain disc and the front view a plain rectangle
  no matter which section came from the vase slice. The weld was right and the demo did not show it.
  `examples/vase_40mm.stl` is now a real profile with a belly, a neck and a flared lip, sliced twice
  by PrusaSlicer 2.9.6 into `examples/vase_normal_40mm.gcode` and `examples/vase_spiral_40mm.gcode`.
  `sim/demo.py` and the README lead image come from those. The test fixtures are untouched, so all
  138 tests still run against the shape that makes weld bugs obvious.

- **The preview was broken on every phone and every HiDPI screen.** No viewport meta tag, and the
  canvases had no CSS width so the device-pixel-ratio resize fed back into layout and grew them to
  1560 px. It was only ever checked at 1180x900 with dpr 1, which is the one configuration that
  hides both faults. Now verified at 360, 390, 414, 768, 1180 and 1440 across dpr 1, 2 and 3, by
  measuring that the drawn content of both canvases lands inside the viewport rather than by
  looking at a screenshot.

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
- **Nobody has printed one.** The comment on #3204 asks for exactly that, so the next real signal
  comes from a stranger, not from here.

## Shipping steps for the owner

1.4.0 is on the local branch `auto-slice`, not pushed. Nothing here has touched the network.

1. Push `auto-slice` and open the PR against `main`. The branch carries the `auto` command, the two
   new modules, three new test files, two new 3MF fixtures, `examples/vase.3mf`, the version bump
   and the changelog entry.
2. Wait for CI green on the head commit, checked through that commit's check-runs API rather than
   `gh run watch`. The end-to-end test skips on CI, which has no PrusaSlicer; that is deliberate.
3. Merge, then `python -m build && python -m twine upload dist/*`.
4. Tag `v1.4.0` on the merge commit and attach the exe, wheel, sdist and standalone `vaseweld.py`.
5. Post the reply drafted in `docs/draft-3204-reply.md` on
   [PrusaSlicer #3204](https://github.com/prusa3d/PrusaSlicer/issues/3204). It answers greenveg
   directly and asks again for someone to print one. The bot auto-closes legacy issues around
   2026-09-23, so this wants posting before then.
6. Still open from 1.3.0: print the hybrid, photograph it, replace `docs/weld-preview.png` as the
   lead image and keep the render lower down where it explains the mechanism. The r/3Dprinting and
   r/prusa3d posts come after, with a print in hand.

Done in earlier releases: the public repo with CI green across Linux/macOS/Windows on Python 3.10
to 3.13; PyPI, where `pip install vaseweld` from a clean venv produces byte-identical output to the
local wheel; the `v1.3.0` release on `26155da` with all 16 checks green; GitHub Pages at
https://booyaka101.github.io/vaseweld/, rebuilt on every push; and the 1.3.0 announcement on #3204
as [#issuecomment-5551899954](https://github.com/prusa3d/PrusaSlicer/issues/3204#issuecomment-5551899954).

## Added in 1.1.0

- **Repeatable `--at`.** Every cut alternates between the two files again, so one run can produce a
  solid base, a vase body and a solid lid. Each seam gets its own travel, retraction match and flow
  ramp. Verified through Klipper (`5_*_vaseweld_two_cuts` plans cleanly) and through the deposition
  model at both seams: 0.425 mm ramping in at layer 62, 0.239 mm ramping out at 149, 0.450 mm again
  at 150.
- **`vaseweld layers FILE`** prints the ladder and the weldable range.

## Next steps, in the order they are worth doing

- **A cut nobody has to choose.** `--at` still wants a number. `auto` has both slices and the mesh,
  so it could suggest one: the lowest Z where the cross-section stops changing much, which is where
  a vase body can start without the spiral having to chase a shape. Print it as a hint first and
  only then consider `--at auto`, because a wrong automatic cut is worse than no automatic cut.
- **`--set KEY=VALUE`, repeatable, appended to both passes.** Today the only way to steer a slice is
  `--load INI`, which means writing a file to change one number. PrusaSlicer takes every config key
  as a flag, so this is a small change. It was left out of 1.4.0 rather than shipped untested next
  to a release: it needs a refusal for keys in `SPIRAL_VASE_OVERRIDES`, which the vase pass has to
  own.
- **Drive OrcaSlicer and BambuStudio.** Both weld fine today, they just have to be sliced by hand.
  Their CLIs take `--slice` plus a settings JSON rather than per-key flags, so the spiral companion
  set becomes a JSON patch instead of seven arguments. Needs its own version gate and its own
  fixtures.
- **A PrusaSlicer 3.x gate that means something.** The current one refuses on the series number
  because 3.0.0-alpha11 refactored the argument parser, which is honest but blunt. When a 3.x
  release exists, run the fixture matrix against it and either widen `VERIFIED_SERIES` or record
  exactly what broke.
- **Cura.** `parser.py` already recognises Cura's `;LAYER:` markers, but Cura writes no config block,
  so `compat.py` would fall back to the first-layer footprint alone, and there is no Cura fixture.
  Do not claim Cura support until there is one.
- **Sequential (`complete_objects`) plates**, where more than one object is legitimate because they
  print one at a time.
