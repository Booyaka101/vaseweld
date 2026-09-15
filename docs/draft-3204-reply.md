# Draft reply for PrusaSlicer #3204

Not posted. Owner reviews and posts.

It replies to greenveg, who commented on 2026-09-05 at 14:06 UTC: "This method does work. I've done
successful prints with it. I just dont like the manual fuckery..." That is the only confirmation
from anyone who has actually printed one.

There is a clock. The legacy-issue bot posted on 2026-09-02 saying the issue auto-closes three weeks
later with no new user comment, with one more warning two weeks in. That puts the second warning
around 2026-09-16 and the close around 2026-09-23. A comment resets it.

## Every claim below, checked

- greenveg's comment: quoted above, fetched from the issue.
- The six settings: read out of `ConfigManipulation.cpp` at tag `version_2.9.6`. The dialog sets
  `perimeters=1`, `top_solid_layers=0`, `fill_density=0`, `support_material=false`,
  `support_material_enforce_layers=0`, `thin_walls=false`. Exactly six, and exactly the six
  vaseweld passes.
- Same six at tag `version_2.8.1`, byte for byte the same block. This kills the caveat I had
  earlier about older versions being different. They are not.
- Only three of the six reach the CLI: `normalize_fdm` in `PrintConfig.cpp` at `version_2.9.6` sets
  `perimeters`, `top_solid_layers` and `fill_density` (plus the two retraction keys the GUI does not
  touch), and never `support_material`, `support_material_enforce_layers` or `thin_walls`.
- The parser claim is upstream's own wording. 3.0.0-alpha11, released 2026-09-01: "the whole code
  for parsing and evaluating the command line arguments was completely refactored."
- "I don't own a printer" matches what the owner already said in this thread on 2026-09-05.
- Klipper's planner runs on every push to main in `.github/workflows/simulate.yml`; the deposition
  measurement is `sim/analyse.py`.
- 1.4.0 is live on PyPI and a clean venv installing it welds the fixtures correctly.

The repo is already linked two comments above in the same thread, by the same account, so the reply
gives the install line rather than the link again.

---

@greenveg that was the bit I hated too, and thank you for saying it printed. You're the only person
I know of who's actually run one of these, so that one line told me more than everything I'd tested.

The manual part is gone in 1.4.0:

    pip install -U vaseweld
    vaseweld auto project.3mf --at 6.0 -o hybrid.gcode

It calls PrusaSlicer's CLI twice, once normally and once in vase mode, and welds the two. The
intermediate files go in a temp dir and get cleaned up, so you never have to look at them.

Getting that right was harder than I expected. Ticking Spiral Vase in the GUI doesn't just set
spiral_vase, it also sets perimeters to 1, top solid layers to 0, fill density to 0, both support
options off and thin walls off. It does that from the dialog that asks whether it should adjust
those settings for you, which never runs headless, and the CLI's own normalise only covers three of
the six. So slicing the vase pass from the command line with just --spiral-vase gives you a
different toolpath than the GUI would have, mostly through thin walls. auto passes all seven
explicitly. Same six in 2.8.1 and 2.9.6, I checked both against the source.

2.9.x only for now, since 3.0.0-alpha11 refactored the entire command line parser and I haven't
worked through what moved.

Since you've got a printer and you've already done this the hard way: if you ever run one through
auto, I'd genuinely like to see the layer where the spiral starts. I still don't own a printer, so
all I have is Klipper's planner saying the moves are legal and a deposition model saying the right
amount of material lands in the right place. Neither of those knows whether that layer actually
sticks to the one under it. No pressure if you'd rather not.
