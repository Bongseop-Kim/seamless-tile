"""Steep diagonal stripe + pelican-face motif showcase.

Same end-to-end custom-motif test as ``examples/pig-stripe`` but with:
  - a steeper stripe angle: 53.13 deg (snaps to the 4/3 slope, hypot 5),
  - a hand-authored single-color pelican silhouette (long pouched beak + eye hole).

The pelican is single-color (motif convention): the eye is a winding hole so the
stripe/ground shows through (``fill-rule`` is outside the sanitizer allowlist).

Usage:
    .venv/bin/python examples/pelican-stripe/generate.py
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

# Pelican in profile, facing right: head + long pouched beak + plump body. The eye
# is a hole made by an opposite-winding subpath (no `fill-rule` needed).
PELICAN_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <path fill="#000000" d="
    M 62 16 Q 73 16 74 27 L 96 31 L 97 34 L 75 38
    Q 87 53 70 52 Q 61 51 58 45 Q 56 58 55 68
    Q 55 85 36 86 Q 14 86 12 64 Q 10 46 30 42
    Q 45 39 50 33 Q 53 22 55 19 Q 57 16 62 16 Z
    M 66 26 a 2.2 2.2 0 1 0 4.4 0 a 2.2 2.2 0 1 0 -4.4 0 Z
  "/>
</svg>
"""

PALETTE = {
    "slots": [
        {"id": "ground", "hex": "#0f3a47"},  # deep teal background
        {"id": "stripe", "hex": "#2f8e9e"},  # teal diagonal stripe
        {"id": "bird", "hex": "#f3ead7"},    # cream pelican
    ]
}
COLORWAYS = [
    {"id": "default", "mapping": {"ground": "#0f3a47", "stripe": "#2f8e9e", "bird": "#f3ead7"}}
]

TILE = 96.0  # 4/3 slope tiles at period = tile/(5k); tile 96 -> 19.2mm stripes


def build_intent(bird_id: str) -> dict:
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
                    "angle": 53.13,       # steeper than pig-stripe; snaps to 4/3 -> seamless
                    "period_mm": 19.2,    # 96 / 5
                    "bands": [{"offset_mm": 0, "width_mm": 8, "color": "stripe"}],
                },
            },
            {
                "id": "pelican_on_stripe",
                "type": "motif",
                "z_order": 2,
                "params": {"motif_id": bird_id, "size_mm": 16, "color": "bird"},
                "placement": {
                    "type": "path_following",
                    "host_layer": "stripe_base",
                    "lane": "center",
                    "spacing_mm": 24,     # must divide tile_mm (96)
                    "phase_mm": 0,
                    "rotation": "fixed",  # keep pelicans upright
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
    bird_id = register_motif(normalize_motif_svg(PELICAN_SVG))
    print("registered pelican motif id:", bird_id)

    svg = generate(build_intent(bird_id)).svg
    with open(f"{OUT}/pelican-stripe-tile.svg", "w") as f:
        f.write(svg)

    tiled, w, h = _tiled_svg(svg, 4, 4)
    with open(f"{OUT}/pelican-stripe-tiled.svg", "w") as f:
        f.write(tiled)

    binary = find_renderer("rsvg-convert")
    if binary:
        png, _ = rasterize(tiled, "png", 200, max(w, h), binary=binary)
        Image.open(io.BytesIO(png)).convert("RGBA").save(f"{OUT}/pelican-stripe-tiled.png")

    print(f"tile {_pattern_dims(svg)} -> 4x4 ({w:g}x{h:g}mm)" + ("  + PNG" if binary else ""))
    print("renderer:", binary or "(none; PNG skipped)")
    print("output dir:", OUT)


if __name__ == "__main__":
    main()
