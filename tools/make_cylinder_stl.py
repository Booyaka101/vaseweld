"""Write the cylinder STL the committed fixtures are sliced from.

Kept in the repo so `tools/regen_fixtures.py` can rebuild every fixture from
scratch; the fixtures themselves are real slicer output, not this file.
"""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path


def cylinder(radius: float, height: float, segments: int) -> list[tuple]:
    tris = []
    ring = [
        (
            radius * math.cos(2 * math.pi * i / segments),
            radius * math.sin(2 * math.pi * i / segments),
        )
        for i in range(segments)
    ]
    for i in range(segments):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % segments]
        tris.append(((0.0, 0.0, 0.0), (x1, y1, 0.0), (x0, y0, 0.0)))
        tris.append(((0.0, 0.0, height), (x0, y0, height), (x1, y1, height)))
        tris.append(((x0, y0, 0.0), (x1, y1, 0.0), (x1, y1, height)))
        tris.append(((x0, y0, 0.0), (x1, y1, height), (x0, y0, height)))
    return tris


def write_binary_stl(path: Path, tris: list[tuple]) -> None:
    with path.open("wb") as fh:
        fh.write(b"vaseweld fixture cylinder".ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            u = tuple(b[i] - a[i] for i in range(3))
            v = tuple(c[i] - a[i] for i in range(3))
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            ln = math.sqrt(sum(k * k for k in n)) or 1.0
            fh.write(struct.pack("<3f", *(k / ln for k in n)))
            for p in (a, b, c):
                fh.write(struct.pack("<3f", *p))
            fh.write(struct.pack("<H", 0))


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the fixture cylinder STL.")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--radius", type=float, default=10.0)
    ap.add_argument("--height", type=float, default=40.0)
    ap.add_argument("--segments", type=int, default=64)
    a = ap.parse_args()
    tris = cylinder(a.radius, a.height, a.segments)
    write_binary_stl(a.out, tris)
    print(f"wrote {a.out} ({len(tris)} triangles)")


if __name__ == "__main__":
    main()
