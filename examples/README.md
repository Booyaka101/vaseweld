# Examples

`vase_40mm.stl` is the model the demo is built from: a 40 mm surface of revolution with a bulging
belly, a narrow neck and a flared lip, so the difference between a solid section and a spiral one is
visible at a glance. `vase_normal_40mm.gcode` and `vase_spiral_40mm.gcode` are PrusaSlicer 2.9.6
slices of it at 0.2 mm layers, with Spiral Vase off and on. They are the two inputs to
`python sim/demo.py` and to the worked example in the README:

    vaseweld weld --normal examples/vase_normal_40mm.gcode \
                  --vase examples/vase_spiral_40mm.gcode \
                  --at 6.2 --at 30.2 -o hybrid.gcode
    vaseweld check hybrid.gcode

`cylinder_40mm.stl` and `cylinder_6mm.stl` are the models the committed test fixtures were sliced
from. A cylinder is the right shape for a fixture and the wrong one for a demo: constant radius
means a bug in the weld has nowhere to hide, but it also means the output does not look like a vase.

Regenerate any of them with:

    python tools/make_stl.py --shape vase -o vase_40mm.stl --rings 120 --segments 96
    python tools/make_stl.py -o cylinder_40mm.stl
    python tools/make_stl.py -o cylinder_6mm.stl --height 6 --radius 8 --segments 48
