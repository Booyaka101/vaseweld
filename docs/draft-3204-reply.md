# Draft reply for PrusaSlicer #3204

Not posted. Owner reviews and posts.

## What greenveg actually said, and what they did not

On 2026-09-05 at 14:06 UTC: "This method does work. I've done successful prints with it. I just dont
like the manual fuckery..."

"This method" is the general slice-twice-and-splice approach done by hand. It is not vaseweld. Three
things say so. The owner's post announcing it went up at 12:43:35 and greenveg replied at 14:06:16,
82 minutes later, and you cannot install, slice, weld and print a vase in 82 minutes. greenveg has
been in this thread since 2022-02-22 asking for vase mode from modifiers, so they have been doing
this a long time. And "manual fuckery" describes splicing the files by hand, which is the thing
1.3.0 already took over.

So nobody has printed a vaseweld file. An earlier version of this draft thanked greenveg for
confirming one printed, which they never said, and that overclaim is the sort of thing that gets
a post picked apart. It is gone.

It cuts the other way too. If greenveg has been hand-splicing, then 1.3.0 already removed the part
they were complaining about and 1.4.0 removes what is left, so the reply leads with that instead of
with a version number.

## There is a clock

The legacy-issue bot posted on 2026-09-02: auto-close three weeks later with no new user comment,
one more warning two weeks in. Second warning around 2026-09-16, close around 2026-09-23. Any
comment resets it.

## Every technical claim, checked against source

- The six settings: `ConfigManipulation.cpp` at tag `version_2.9.6` sets `perimeters=1`,
  `top_solid_layers=0`, `fill_density=0`, `support_material=false`,
  `support_material_enforce_layers=0`, `thin_walls=false`. Exactly six, exactly the six vaseweld
  passes.
- Identical block at tag `version_2.8.1`, so older builds do not differ.
- Only three of the six reach the CLI: `normalize_fdm` in `PrintConfig.cpp` at `version_2.9.6` sets
  `perimeters`, `top_solid_layers`, `fill_density` and the two retraction keys the GUI never
  touches, and never `support_material`, `support_material_enforce_layers` or `thin_walls`.
- The parser claim is upstream's wording from the 3.0.0-alpha11 notes, released 2026-09-01: "the
  whole code for parsing and evaluating the command line arguments was completely refactored."
- Klipper's planner runs on every push to main via `.github/workflows/simulate.yml`; the deposition
  measurement is `sim/analyse.py`.

## Register

The owner's own comment in this thread on 2026-09-05 was 619 characters, got no downvotes and drew
a substantive reply in 83 minutes. That is the length and voice to match. An earlier draft here ran
to 1400 polished characters, which is the thing that reads as machine-written. This one is ~700.

The repo is linked two comments above by the same account, so this gives the install line instead.

---

If you're splicing them by hand, you don't have to any more. 1.4.0 does the whole thing:

    pip install -U vaseweld
    vaseweld auto project.3mf --at 6.0 -o hybrid.gcode

It runs PrusaSlicer twice itself, once normal and once in vase mode, then welds the two.

One thing that bit me and would bite anyone doing it manually: ticking Spiral Vase in the GUI also
flips six other settings, and it does that from a dialog that never runs headless. So a vase pass
sliced from the command line with just --spiral-vase isn't the same toolpath the GUI gives you.
2.9.x only for now, 3.0 rewrote the argument parsing.

As far as I know nobody has actually printed one of the welded files yet. If you run one I'd really
like to see the layer where the spiral starts.
