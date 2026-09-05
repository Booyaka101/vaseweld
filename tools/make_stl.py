"""Write the STL models the fixtures and the demo are sliced from.

Two shapes, one surface of revolution each. The cylinder is the test fixture:
constant radius, so a bug in the weld has nowhere to hide. The vase is the demo,
because a demo of spiral vase mode should look like a vase.

    python tools/make_stl.py --shape vase -o vase_40mm.stl
"""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path

# (height fraction, radius in mm), splined into a smooth profile.
PROFILES = {
    "cylinder": [(0.0, 10.0), (1.0, 10.0)],
    "vase": [
        (0.00, 11.0),  # foot
        (0.08, 13.5),
        (0.26, 19.0),  # belly
        (0.40, 20.0),  # widest
        (0.60, 15.0),
        (0.78, 9.0),  # neck
        (0.88, 9.5),
        (1.00, 13.0),  # flared lip
    ],
}


def catmull_rom(points: list[tuple[float, float]], t: float) -> float:
    """Radius at height fraction ``t``, smoothed through the control points."""
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    i = max(i for i, (u, _) in enumerate(points) if u <= t)
    i = min(i, len(points) - 2)
    padded = [points[0]] + points + [points[-1]]
    p0, p1, p2, p3 = (padded[i + k][1] for k in range(4))
    span = points[i + 1][0] - points[i][0]
    u = (t - points[i][0]) / span if span else 0.0
    return 0.5 * (
        2 * p1
        + (-p0 + p2) * u
        + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u
        + (-p0 + 3 * p1 - 3 * p2 + p3) * u * u * u
    )


def revolve(profile: list[tuple[float, float]], height: float, rings: int, segments: int):
    """A closed solid of revolution: side wall plus a flat cap at each end."""
    levels = [(i / rings * height, catmull_rom(profile, i / rings)) for i in range(rings + 1)]
    ring = [
        (math.cos(2 * math.pi * s / segments), math.sin(2 * math.pi * s / segments))
        for s in range(segments)
    ]
    tris = []
    for s in range(segments):
        (cx0, cy0), (cx1, cy1) = ring[s], ring[(s + 1) % segments]
        for (z0, r0), (z1, r1) in zip(levels, levels[1:]):
            a = (cx0 * r0, cy0 * r0, z0)
            b = (cx1 * r0, cy1 * r0, z0)
            c = (cx1 * r1, cy1 * r1, z1)
            d = (cx0 * r1, cy0 * r1, z1)
            tris += [(a, b, c), (a, c, d)]
        for z, r, flip in (
            (levels[0][0], levels[0][1], True),
            (levels[-1][0], levels[-1][1], False),
        ):
            p, q = (cx0 * r, cy0 * r, z), (cx1 * r, cy1 * r, z)
            tris.append(((0.0, 0.0, z), q, p) if flip else ((0.0, 0.0, z), p, q))
    return tris


def write_binary_stl(path: Path, tris: list[tuple], label: str) -> None:
    with path.open("wb") as fh:
        fh.write(label.encode()[:79].ljust(80, bytes([0])))
        fh.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            u = tuple(b[i] - a[i] for i in range(3))
            v = tuple(c[i] - a[i] for i in range(3))
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            length = math.sqrt(sum(k * k for k in n)) or 1.0
            fh.write(struct.pack("<3f", *(k / length for k in n)))
            for point in (a, b, c):
                fh.write(struct.pack("<3f", *point))
            fh.write(struct.pack("<H", 0))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--shape", choices=sorted(PROFILES), default="cylinder")
    ap.add_argument("--height", type=float, default=40.0)
    ap.add_argument("--radius", type=float, help="scale a cylinder to this radius")
    ap.add_argument("--rings", type=int, default=80)
    ap.add_argument("--segments", type=int, default=64)
    args = ap.parse_args()

    profile = PROFILES[args.shape]
    if args.radius and args.shape == "cylinder":
        profile = [(t, args.radius) for t, _ in profile]
    rings = 1 if args.shape == "cylinder" else args.rings
    tris = revolve(profile, args.height, rings, args.segments)
    write_binary_stl(args.out, tris, f"vaseweld {args.shape}")
    print(f"wrote {args.out} ({len(tris)} triangles)")


if __name__ == "__main__":
    main()
