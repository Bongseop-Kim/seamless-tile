"""Render the session-5 feature showcases in this directory.

Each showcase is written three ways:
  <name>-tile.svg   single pattern tile
  <name>-tiled.svg  4x4 repeat, to eyeball seam continuity in a browser
  <name>-tiled.png  rasterized 4x4 repeat (needs rsvg-convert; skipped if absent)

Usage (from anywhere; the script bootstraps the repo root onto sys.path):
    .venv/bin/python examples/session5/generate.py

Outputs land next to this file. Edit SHOWCASES to add intents.
"""

import io
import os
import sys
import xml.etree.ElementTree as ET

# Bootstrap repo root (two levels up) so `app` imports without PYTHONPATH.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402

from app.engine.generate import generate  # noqa: E402
from app.render.raster import find_renderer, rasterize  # noqa: E402
from app.render.svg import render_svg_document  # noqa: E402

OUT = _HERE
NS = "{http://www.w3.org/2000/svg}"
PALETTE = {
    "slots": [
        {"id": "ground", "hex": "#10243a"},
        {"id": "accent", "hex": "#ef8a7a"},
        {"id": "gold", "hex": "#f5ca57"},
    ]
}
COLORWAYS = [
    {"id": "default", "mapping": {"ground": "#10243a", "accent": "#ef8a7a", "gold": "#f5ca57"}}
]


def base(layers, seed=7, tile=48):
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": tile, "dpi": 300},
        "seed": seed,
        "production": {"method": "digital", "max_colors": 12},
        "palette": PALETTE,
        "colorways": COLORWAYS,
        "layers": layers,
    }


def bg():
    return {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "ground"}}


def motif(mid, color, size, placement, zid, z):
    return {
        "id": zid,
        "type": "motif",
        "z_order": z,
        "params": {"motif_id": mid, "size_mm": size, "color": color},
        "placement": placement,
    }


SHOWCASES = {
    "01-lattice-halfdrop-dots": base([
        bg(),
        motif("circle", "accent", 4.0,
              {"type": "lattice", "lattice": {"cell_w_mm": 12, "cell_h_mm": 12,
                                              "drop_fraction": 0.5, "drop_axis": "column"}},
              "dots", 1),
    ]),
    "02-scatter-poisson-bluenoise": base([
        bg(),
        motif("circle", "accent", 3.0,
              {"type": "scatter", "scatter": {"mode": "poisson", "min_dist_mm": 7}},
              "dots", 1),
    ]),
    "03-scatter-sateen": base([
        bg(),
        motif("circle", "gold", 4.0,
              {"type": "scatter", "scatter": {"mode": "sateen", "sateen_n": 6, "sateen_step": 5}},
              "dots", 1),
    ]),
    "04-wave-lane-vine": base([
        bg(),
        motif("circle", "accent", 2.2,
              {"type": "path_following", "spacing_mm": 4, "rotation": "follow_path",
               "path": {"kind": "wave", "angle": 0, "wavelength": 24, "amplitude": 8}},
              "vine", 1),
        motif("bee", "gold", 6.0,
              {"type": "path_following", "spacing_mm": 24, "phase_mm": 12, "rotation": "follow_path",
               "path": {"kind": "wave", "angle": 0, "wavelength": 24, "amplitude": 8}},
              "bees", 2),
    ]),
    "06-point-set-anchors": base([
        bg(),
        motif("circle", "accent", 5.0,
              {"type": "point_set", "point_set": {"points": [[8, 8], [40, 8], [24, 24],
                                                             [8, 40], [40, 40]]}},
              "dots", 1),
    ]),
}


def _pattern_dims(svg):
    p = ET.fromstring(svg).find(f".//{NS}pattern")
    return float(p.get("width")), float(p.get("height"))


def _tiled_svg(svg, cols, rows):
    defs = svg[svg.index("<defs>") + len("<defs>"): svg.index("</defs>")]
    pw, ph = _pattern_dims(svg)
    w, h = pw * cols, ph * rows
    body = f'<rect x="0" y="0" width="{w}" height="{h}" fill="url(#tile)"/>'
    return render_svg_document(body, w, h, defs=defs), w, h


def main():
    binary = find_renderer("rsvg-convert")
    for name, intent in SHOWCASES.items():
        svg = generate(intent).svg
        with open(f"{OUT}/{name}-tile.svg", "w") as f:
            f.write(svg)
        tiled, w, h = _tiled_svg(svg, 4, 4)
        with open(f"{OUT}/{name}-tiled.svg", "w") as f:
            f.write(tiled)
        if binary:
            png, _ = rasterize(tiled, "png", 200, max(w, h), binary=binary)
            Image.open(io.BytesIO(png)).convert("RGBA").save(f"{OUT}/{name}-tiled.png")
        print(f"{name}: tile {_pattern_dims(svg)} -> 4x4 ({w:g}x{h:g}mm)"
              + ("  + PNG" if binary else ""))
    print("\nrenderer:", binary or "(none; PNGs skipped)")
    print("output dir:", OUT)


if __name__ == "__main__":
    main()
