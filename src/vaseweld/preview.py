"""Turn a G-code file into a self-contained HTML page you can open and scrub through.

No dependencies and no server: the toolpath is embedded in the page, drawn to scale
with each bead's real width, and coloured by which slice it came from. Dragging the
layer slider past the weld shows the change from stacked layers to a spiral.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .parser import Cursor, GcodeFile, bead_width, config_float, parse_file, walk

_BANNER_CUTS = re.compile(r"layer (\d+)\)")
_BANNER_SLAB = re.compile(r";\s+layers (\d+)-(\d+) from (\S+) \((\w+)\)")

# Colour by source, matching the images in the README.
NORMAL = "#4682be"
VASE = "#e1783c"
_WIDTH_STEP = 0.05  # mm; a path is broken when its bead changes bucket


@dataclass
class Section:
    """One run of layers, as named by the ``; vaseweld`` banner in the file."""

    role: str
    source: str
    first: int
    last: int


@dataclass
class Preview:
    name: str
    layers: list[dict] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    cuts: list[int] = field(default_factory=list)
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


def read_banner(gcode: GcodeFile) -> tuple[list[Section], list[int]]:
    """Recover the weld layout from the ``; vaseweld`` block, if the file has one."""
    sections: list[Section] = []
    cuts: list[int] = []
    for line in gcode.lines[: gcode.header_end]:
        if line.startswith("; vaseweld ") and "welded at" in line:
            cuts = [int(n) for n in _BANNER_CUTS.findall(line)]
        match = _BANNER_SLAB.match(line)
        if match:
            sections.append(
                Section(
                    role=match.group(4),
                    source=match.group(3),
                    first=int(match.group(1)),
                    last=int(match.group(2)),
                )
            )
    if sum(s.last - s.first + 1 for s in sections) != len(gcode.layers):
        return [], []
    return sections, cuts


def layer_paths(
    lines: list[str], cursor: Cursor, layer_height: float, filament: float
) -> list[dict]:
    """Extruding moves of one layer, joined into polylines of near-constant width.

    Takes the running cursor rather than a layer index: layers are contiguous, so
    walking the file once is O(n) where re-deriving the state per layer is O(n^2).
    """
    area_of_filament = math.pi * (filament / 2.0) ** 2
    paths: list[dict] = []
    points: list[float] = []
    bucket = None
    previous = (cursor.x, cursor.y)

    def flush() -> None:
        nonlocal points, bucket
        if len(points) >= 4 and bucket is not None:
            paths.append({"w": round(bucket, 3), "p": points})
        points = []
        bucket = None

    for step in walk(lines, cursor):
        if step.kind != "move":
            continue
        here = (cursor.x, cursor.y)
        printing = step.delta and step.delta > 0 and step.dist > 0 and None not in previous
        if not printing:
            flush()
            previous = here
            continue
        width = bead_width(step.delta * area_of_filament / step.dist, layer_height)
        rounded = round(width / _WIDTH_STEP) * _WIDTH_STEP
        if bucket is None:
            bucket = rounded
            points = [round(previous[0], 2), round(previous[1], 2)]
        elif abs(rounded - bucket) > 1e-9 or (points[-2], points[-1]) != (
            round(previous[0], 2),
            round(previous[1], 2),
        ):
            flush()
            bucket = rounded
            points = [round(previous[0], 2), round(previous[1], 2)]
        points += [round(here[0], 2), round(here[1], 2)]
        previous = here
    flush()
    return paths


def build(gcode: GcodeFile) -> Preview:
    layer_height = config_float(gcode.config, "layer_height", 0.2)
    filament = config_float(gcode.config, "filament_diameter", 1.75)
    sections, cuts = read_banner(gcode)
    preview = Preview(name=gcode.path.name, sections=sections, cuts=cuts)

    xs: list[float] = []
    ys: list[float] = []
    cursor = gcode.cursor()
    for _ in walk(gcode.lines[: gcode.layers[0].start], cursor):
        pass  # the start gcode leaves the nozzle somewhere; layer 1 continues from it
    for layer in gcode.layers:
        paths = layer_paths(gcode.layer_lines(layer), cursor, layer_height, filament)
        preview.layers.append({"z": round(layer.z, 3), "paths": paths})
        for path in paths:
            xs += path["p"][0::2]
            ys += path["p"][1::2]
    if xs:
        preview.bounds = (min(xs), min(ys), max(xs), max(ys))
    return preview


def render(preview: Preview) -> str:
    """The whole page: data, drawing code and controls in one file."""
    roles = ["normal"] * len(preview.layers)
    for section in preview.sections:
        for index in range(section.first - 1, min(section.last, len(roles))):
            roles[index] = section.role
    data = {
        "name": preview.name,
        "bounds": [round(v, 2) for v in preview.bounds],
        "cuts": preview.cuts,
        "roles": roles,
        "sections": [
            {"role": s.role, "source": s.source, "first": s.first, "last": s.last}
            for s in preview.sections
        ],
        "layers": preview.layers,
    }
    legend = "".join(
        f'<span class="key"><i style="background:{VASE if s.role == "vase" else NORMAL}"></i>'
        f"layers {s.first}-{s.last} from {s.source} ({s.role})</span>"
        for s in preview.sections
    )
    return (
        _TEMPLATE.replace("__TITLE__", preview.name)
        .replace("__LEGEND__", legend)
        .replace("__DATA__", json.dumps(data, separators=(",", ":")))
        .replace("__NORMAL__", NORMAL)
        .replace("__VASE__", VASE)
    )


def write(path: Path, gcode_path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(build(parse_file(gcode_path))), encoding="utf-8")
    return path


_TEMPLATE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>__TITLE__ - vaseweld preview</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; font: 14px/1.5 system-ui, sans-serif; background: #fafaf9; color: #3c3c41; }
  header { padding: 14px 18px 8px; }
  h1 { margin: 0; font-size: 16px; font-weight: 600; }
  .sub { color: #85858c; font-size: 13px; }
  .legend { margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; }
  .key { display: flex; align-items: center; gap: 6px; }
  .key i { width: 11px; height: 11px; border-radius: 2px; display: inline-block; }
  main { display: flex; gap: 18px; padding: 0 18px 18px; flex-wrap: wrap; }
  canvas { background: #fff; border: 1px solid #e6e6e3; border-radius: 6px; touch-action: none; }
  .panel { display: flex; flex-direction: column; gap: 6px; }
  .cap { font-size: 12px; color: #85858c; }
  .controls { padding: 0 18px 20px; display: flex; align-items: center; gap: 12px;
              flex-wrap: wrap; max-width: 1100px; }
  input[type=range] { flex: 1 1 320px; }
  button { font: inherit; padding: 4px 12px; border: 1px solid #d0d0cc; border-radius: 5px;
           background: #fff; cursor: pointer; }
  button:hover { background: #f2f2ef; }
  .readout { font-variant-numeric: tabular-nums; min-width: 190px; }
  label { display: flex; align-items: center; gap: 6px; }
  @media (prefers-color-scheme: dark) {
    body { background: #17171a; color: #dcdce0; }
    canvas { background: #1f1f23; border-color: #34343a; }
    button { background: #232329; color: inherit; border-color: #3a3a42; }
    button:hover { background: #2b2b32; }
  }
</style>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">Every bead drawn at the width the G-code actually asks for.
    Drag the slider past a weld to watch stacked layers become a spiral.</div>
  <div class="legend">__LEGEND__</div>
</header>
<main>
  <div class="panel"><canvas id="top" width="520" height="520"></canvas>
    <div class="cap">from above</div></div>
  <div class="panel"><canvas id="side" width="380" height="520"></canvas>
    <div class="cap">from the front</div></div>
</main>
<div class="controls">
  <button id="play">Play</button>
  <input type="range" id="slider" min="1" value="1">
  <span class="readout" id="readout"></span>
  <label><input type="checkbox" id="stack"> stack the top view</label>
</div>
<script>
// Wrapped: a bare `const top` at global scope collides with the non-configurable
// window.top and silently stops the whole script from running.
(function () {
const DATA = __DATA__;
const COLOUR = { normal: "__NORMAL__", vase: "__VASE__" };
const layers = DATA.layers, roles = DATA.roles, cuts = new Set(DATA.cuts);
const [minX, minY, maxX, maxY] = DATA.bounds;
const minZ = layers[0].z, maxZ = layers[layers.length - 1].z;
const topView = document.getElementById("top"), sideView = document.getElementById("side");
const slider = document.getElementById("slider"), readout = document.getElementById("readout");
const stack = document.getElementById("stack"), play = document.getElementById("play");
slider.max = layers.length;
slider.value = layers.length;

function fit(canvas, w, h) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * dpr;
  canvas.height = canvas.clientHeight * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const pad = 14;
  const scale = Math.min((canvas.clientWidth - 2 * pad) / w, (canvas.clientHeight - 2 * pad) / h);
  return { ctx, scale, pad };
}

function strokePath(ctx, pts, project) {
  ctx.beginPath();
  for (let i = 0; i < pts.length; i += 2) {
    const [x, y] = project(pts[i], pts[i + 1]);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.stroke();
}

function draw() {
  const upto = +slider.value;
  const z = layers[upto - 1].z;
  readout.textContent = `layer ${upto} of ${layers.length}   Z ${z.toFixed(2)} mm` +
    (cuts.has(upto) ? "   \\u2190 weld" : "");

  const w = maxX - minX, h = maxY - minY;
  const a = fit(topView, w, h);
  a.ctx.clearRect(0, 0, topView.clientWidth, topView.clientHeight);
  const ox = (topView.clientWidth - w * a.scale) / 2, oy = (topView.clientHeight - h * a.scale) / 2;
  const toTop = (x, y) => [ox + (x - minX) * a.scale, topView.clientHeight - oy - (y - minY) * a.scale];

  const zSpan = Math.max(maxZ - minZ, 0.001);
  const b = fit(sideView, w, zSpan);
  b.ctx.clearRect(0, 0, sideView.clientWidth, sideView.clientHeight);
  const sx = (sideView.clientWidth - w * b.scale) / 2, sy = (sideView.clientHeight - zSpan * b.scale) / 2;
  const toSide = (x, zz) => [sx + (x - minX) * b.scale,
                             sideView.clientHeight - sy - (zz - minZ) * b.scale];

  a.ctx.lineCap = b.ctx.lineCap = "round";
  a.ctx.lineJoin = b.ctx.lineJoin = "round";
  // The front view builds up so you can see the sections stack. The top view shows
  // the layer under the slider, because 200 stacked loops is just a filled disc.
  const topFrom = stack.checked ? 1 : Math.max(1, upto - 1);
  for (let i = 1; i <= upto; i++) {
    const layer = layers[i - 1];
    const colour = COLOUR[roles[i - 1]] || COLOUR.normal;
    b.ctx.globalAlpha = i === upto ? 1 : 0.35 + 0.65 * (i / upto);
    b.ctx.strokeStyle = colour;
    for (const path of layer.paths) {
      b.ctx.lineWidth = Math.max(path.w * b.scale, 0.6);
      b.ctx.beginPath();
      for (let k = 0; k < path.p.length; k += 2) {
        const [px, py] = toSide(path.p[k], layer.z);
        k ? b.ctx.lineTo(px, py) : b.ctx.moveTo(px, py);
      }
      b.ctx.stroke();
    }
    if (i < topFrom) continue;
    a.ctx.globalAlpha = i === upto ? 1 : 0.22;
    a.ctx.strokeStyle = colour;
    for (const path of layer.paths) {
      a.ctx.lineWidth = Math.max(path.w * a.scale, 0.6);
      strokePath(a.ctx, path.p, toTop);
    }
  }
  a.ctx.globalAlpha = b.ctx.globalAlpha = 1;
}

let timer = null;
play.onclick = () => {
  if (timer) { clearInterval(timer); timer = null; play.textContent = "Play"; return; }
  play.textContent = "Pause";
  if (+slider.value >= layers.length) slider.value = 1;
  timer = setInterval(() => {
    slider.value = +slider.value + 1;
    draw();
    if (+slider.value >= layers.length) { clearInterval(timer); timer = null; play.textContent = "Play"; }
  }, 1000 / 60);
};
slider.oninput = draw;
stack.onchange = draw;
addEventListener("resize", draw);
draw();
})();
</script>
</html>
"""
