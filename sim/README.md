# Klipper simulation

The unit tests prove the welded file is well formed. This proves a printer would accept it,
by feeding it to a real printer firmware.

`klippy` is Klipper's host process. Given `-i file.gcode` it runs in batch mode: it parses the
G-code, plans every move through the real cartesian kinematics, and applies the real extruder
limits, without a printer attached. Anything klippy refuses to plan is something the printer
would refuse too.

## Running it

Needs Docker. Takes about two minutes the first time, seconds after that.

```sh
docker build -t klipper-batch sim/
python sim/build_cases.py
docker run --rm -v "$PWD/sim:/work" klipper-batch bash /work/run.sh
python sim/analyse.py
```

`build_cases.py` writes five files per extruder mode from the committed fixtures: the two slicer
originals, the splice you get by pasting the two files together in a text editor, and vaseweld's
weld in both directions.

## What it found

```
PASS  1_absolute_source_normal           print time 368.737s
PASS  1_absolute_source_vase             print time 305.882s
PASS  1_relative_source_normal           print time 368.737s
PASS  1_relative_source_vase             print time 306.009s
FAIL  2_absolute_naive_text_editor       klippy exit 255, 40 rejected moves
        Move exceeds maximum extrusion (10.116mm^2 vs 0.640mm^2)
        Move exceeds maximum extrusion (10.635mm^2 vs 0.640mm^2)
        Move exceeds maximum extrusion (11.232mm^2 vs 0.640mm^2)
PASS  2_relative_naive_text_editor       print time 304.025s
PASS  3_absolute_vaseweld                print time 299.385s
PASS  3_relative_vaseweld                print time 299.512s
PASS  4_absolute_vaseweld_vase_first     print time 371.931s
PASS  4_relative_vaseweld_vase_first     print time 371.931s
```

Three things worth pulling out.

**The hand splice of absolute-E files is rejected outright.** That is PrusaSlicer's default extruder
mode, so it is the common case. klippy exits 255 after refusing 40 moves, the worst asking for
72.5 mm² of extrusion against a 0.64 mm² limit. Every E value after the paste is read as an absolute
position, so the first one asks the extruder for 33 mm of filament in a single 12.5 mm move.

**vaseweld's output plans cleanly in every case,** and its log is exactly as clean as the two slicer
originals: the only warning in any of them is a `Reactor busy` scheduler notice that appears in the
untouched slicer files too.

**The print times cross-check.** Welding normal-below takes 299.4s and vase-below takes 371.9s;
299.4 + 371.9 = 671.3 against 368.7 + 305.9 = 674.6 for the two whole files. The two complementary
welds add up to the two originals, minus the seam moves.

## What klippy cannot see

A splice of *relative*-E files plans fine, because concatenating relative deltas does not produce
impossible numbers. It is still wrong, and `analyse.py` measures how:

```
case                                  travel   filament   flow vs normal
-------------------------------------------------------------------------
2_absolute_naive_text_editor        12.522 mm 33.03150 mm         7793.6%
3_absolute_vaseweld                  0.500 mm  0.01359 mm           80.2%
2_relative_naive_text_editor        12.522 mm  0.01694 mm            4.0%
3_relative_vaseweld                  0.500 mm  0.01359 mm           80.2%
-------------------------------------------------------------------------
```

A hand splice leaves the nozzle wherever the first file stopped, and the spiral starts 12.5 mm away.
So the first move after the paste is a 12.5 mm line drawn straight across the part: at 4% of the
normal flow rate in the relative case, which is a scar, and at 7794% in the absolute case, which is
the blob klippy refuses. vaseweld retracts, travels there, unretracts, so its first printing move is
0.5 mm long at 80.2% flow. That 80.2% is the flow ramp starting from `--start-flow 0.8`, which is
the ramp doing exactly what it says on the tin.

## The simulated printer

`printer.cfg` is a plain cartesian printer with a 200x200 bed, one extruder, 1.75 mm filament and a
0.4 mm nozzle, matching how the fixtures were sliced. The extruder limits are Klipper's defaults, so
`max_extrude_cross_section` is `4 * 0.4²` = 0.64 mm². Raising it would make the comparison
meaningless, so it is left alone.

The firmware is built for Klipper's `linux` MCU target, which compiles with native gcc and needs no
cross compiler. `Dockerfile` does that and installs klippy's Python dependencies.
