"""Diagonal stripe + pig-face motif showcase.

Demonstrates testing a *custom* motif end to end:
  1. author a single-color pig-face SVG,
  2. register it via ``normalize_motif_svg`` + ``register_motif`` (content-hash id),
  3. reference that id from a ``path_following`` motif layer riding the stripe lane,
  4. render a single seamless tile + a 4x4 repeat (SVG and PNG) to eyeball the seam.

The pig is single-color (motif convention): internal features are punched as
``fill-rule="evenodd"`` holes so the stripe/ground shows through (eyes, snout),
with the two nostrils as solid islands nested inside the snout hole.

Usage:
    .venv/bin/python examples/pig-stripe/generate.py
"""

import io
import os
import sys
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402

from app.engine.generate import generate  # noqa: E402
from app.motifs.registry import normalize_motif_svg, register_motif  # noqa: E402
from app.render.raster import find_renderer, rasterize  # noqa: E402
from app.render.svg import render_svg_document  # noqa: E402

OUT = _HERE
NS = "{http://www.w3.org/2000/svg}"

# `fill-rule` is outside the sanitizer allowlist, so holes are made by *winding*:
# the body outline runs one way; eyes + snout subpaths run the opposite way
# (nonzero -> holes), and the nostrils run the body's way again (solid islands
# nested inside the snout hole). Sweep flags set the direction.
PIG_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <path fill="#000000" d="
    M 16 56 Q 14 40 26 34 L 20 18 L 40 34 Q 50 30 60 34 L 80 18 L 74 34
    Q 86 40 84 56 Q 84 78 64 88 Q 50 94 36 88 Q 16 78 16 56 Z
    M 34 52 a 4 4 0 1 0 8 0 a 4 4 0 1 0 -8 0 Z
    M 58 52 a 4 4 0 1 0 8 0 a 4 4 0 1 0 -8 0 Z
    M 30 68 a 20 14 0 1 0 40 0 a 20 14 0 1 0 -40 0 Z
    M 40 68 a 3 6 0 1 1 6 0 a 3 6 0 1 1 -6 0 Z
    M 54 68 a 3 6 0 1 1 6 0 a 3 6 0 1 1 -6 0 Z
  "/>
</svg>
"""

PALETTE = {
    "slots": [
        {"id": "ground", "hex": "#f3e3d3"},  # cream background
        {"id": "stripe", "hex": "#e79aae"},  # pink diagonal stripe
        {"id": "pig", "hex": "#5b2a36"},     # plum pig face
    ]
}
COLORWAYS = [
    {"id": "default", "mapping": {"ground": "#f3e3d3", "stripe": "#e79aae", "pig": "#5b2a36"}}
]

TILE = 96.0  # 3-4-5 slope tiles at period = tile/(5k); tile 96 -> 19.2mm stripes


def build_intent(pig_id: str) -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": TILE, "dpi": 300},
        "seed": 7,
        "production": {"method": "digital", "max_colors": 12},
        "palette": PALETTE,
        "colorways": COLORWAYS,
        "layers": [
            {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "ground"}},
            {
                "id": "stripe_base",
                "type": "stripe",
                "z_order": 1,
                "params": {
                    "angle": -36.87,      # snaps to the 3-4-5 slope -> seamless
                    "period_mm": 19.2,    # 96 / 5
                    "bands": [{"offset_mm": 0, "width_mm": 8, "color": "stripe"}],
                },
            },
            {
                "id": "pig_on_stripe",
                "type": "motif",
                "z_order": 2,
                "params": {"motif_id": pig_id, "size_mm": 16, "color": "pig"},
                "placement": {
                    "type": "path_following",
                    "host_layer": "stripe_base",
                    "lane": "center",
                    "spacing_mm": 24,
                    "phase_mm": 0,
                    "rotation": "fixed",  # keep pigs upright, not tilted to the lane
                },
            },
        ],
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
    pig_id = register_motif(normalize_motif_svg(PIG_SVG))
    print("registered pig motif id:", pig_id)

    svg = generate(build_intent(pig_id)).svg
    with open(f"{OUT}/pig-stripe-tile.svg", "w") as f:
        f.write(svg)

    tiled, w, h = _tiled_svg(svg, 4, 4)
    with open(f"{OUT}/pig-stripe-tiled.svg", "w") as f:
        f.write(tiled)

    binary = find_renderer("rsvg-convert")
    if binary:
        png, _ = rasterize(tiled, "png", 200, w, h, binary=binary)
        Image.open(io.BytesIO(png)).convert("RGBA").save(f"{OUT}/pig-stripe-tiled.png")

    print(f"tile {_pattern_dims(svg)} -> 4x4 ({w:g}x{h:g}mm)" + ("  + PNG" if binary else ""))
    print("renderer:", binary or "(none; PNG skipped)")
    print("output dir:", OUT)


if __name__ == "__main__":
    main()
