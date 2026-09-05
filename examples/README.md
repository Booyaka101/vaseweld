# Examples

`cylinder_40mm.stl` and `cylinder_6mm.stl` are the models the committed test fixtures were sliced
from. Regenerate them with:

    python tools/make_cylinder_stl.py -o cylinder_40mm.stl
    python tools/make_cylinder_stl.py -o cylinder_6mm.stl --height 6 --radius 8 --segments 48

To reproduce the worked example in the README without a slicer, use the G-code already committed
under `tests/fixtures/`:

    vaseweld weld --normal tests/fixtures/prusaslicer_normal_40mm.gcode \
                  --vase tests/fixtures/prusaslicer_vase_40mm.gcode \
                  --at 12.4 -o hybrid.gcode
    vaseweld check hybrid.gcode
