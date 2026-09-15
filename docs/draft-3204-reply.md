# Draft reply for PrusaSlicer #3204

Not posted. Owner reviews and posts. It replies to greenveg, who said on 2026-09-05 that the
slice-twice-and-weld method works and that they have printed with it, but that they don't like the
manual part.

Posting it also resets the legacy-issue bot's clock, which starts auto-closing three weeks after
2026-09-02.

---

@greenveg the manual part is gone in 1.4.0. `vaseweld auto project.3mf --at 6.0 -o hybrid.gcode`
drives PrusaSlicer's own CLI, once normally and once in vase mode, and welds the two. You never see
the intermediate files.

The checkbox turns out not to be the whole story either. Ticking Spiral Vase in the GUI also changes
six other settings, and it does that from a dialog that never runs headless, so slicing the vase pass
from the command line with just `--spiral-vase` gives you a slightly different toolpath than the GUI
would have. auto passes all seven. PrusaSlicer 2.9.x only for now, since 3.0.0-alpha11 rewrote the
argument parsing.

Since you've actually printed these, any chance you'd run one and photograph the layer where the
spiral starts? I still don't own a printer, so all I have is Klipper's planner saying the moves are
legal and a bead model saying the material lands where it should. Neither of those knows anything
about whether the layer sticks.
