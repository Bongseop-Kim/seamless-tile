"""Multi-color pelican motif showcase — exercises the S12 multicolor engine.

Unlike ``examples/pelican-stripe`` (single-color silhouette), this pelican is authored
with **five distinct fill colors** (body / wing / beak / pouch / eye). The engine:

  1. ``normalize_motif_svg`` maps the five colors to motif-local slots ``s0..s4`` in
     document DFS first-appearance order (drawn bottom->top so slot order == z-order),
  2. the intent binds each motif slot to a palette slot via ``params.colors``,
  3. ``compose`` stacks one ``<use color=…>`` per slot — the ``<symbol>`` stays
     colorway-agnostic, so swapping the colorway recolors every part without touching
     a single symbol definition (D15 dedup).

Two colorways ("default" natural, "dusk" recolored) prove the colorway-agnostic invariant.

Usage:
    .venv/bin/python examples/pelican-multicolor/generate.py
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

from app.engine.composition import compose  # noqa: E402
from app.engine.generate import generate  # noqa: E402
from app.motifs.registry import (  # noqa: E402
    normalize_motif_svg,
    register_motif,
    slot_render_symbols,
)
from app.render.raster import find_renderer, rasterize  # noqa: E402
from app.render.svg import render_svg_document  # noqa: E402
from app.validate.intent import validate_intent  # noqa: E402

OUT = _HERE
NS = "{http://www.w3.org/2000/svg}"

# Pelican in profile, facing right. Parts are drawn bottom->top, each a DISTINCT color
# so document DFS order == slot order == paint (z) order:
#   1 body silhouette (cream)  2 wing (slate)  3 upper beak (orange)
#   4 throat pouch (gold)      5 eye (near-black)
PELICAN_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <path fill="#f3ead7" d="
    M 62 16 Q 73 16 74 27 L 96 31 L 97 34 L 75 38
    Q 87 53 70 52 Q 61 51 58 45 Q 56 58 55 68
    Q 55 85 36 86 Q 14 86 12 64 Q 10 46 30 42
    Q 45 39 50 33 Q 53 22 55 19 Q 57 16 62 16 Z"/>
  <path fill="#5b7c99" d="
    M 21 56 Q 33 49 45 58 Q 39 71 26 71 Q 18 67 21 56 Z"/>
  <path fill="#e8943a" d="
    M 60 21 L 95 31 L 96 33.5 L 75 37 Q 64 34 59.5 29 Z"/>
  <path fill="#f2c14e" d="
    M 75 37.5 Q 87 53 70 51.5 Q 61.5 50.5 58.5 44.5 Q 66 40 75 37.5 Z"/>
  <circle cx="64.5" cy="26" r="2.4" fill="#1a1a1a"/>
</svg>
"""

# Palette slots: a teal ground + the five pelican parts. The motif's authoring colors
# are tokenized away; THESE colorway values are what actually render.
PALETTE = {
    "slots": [
        {"id": "ground", "hex": "#0e3b46"},
        {"id": "body", "hex": "#f3ead7"},
        {"id": "wing", "hex": "#5b7c99"},
        {"id": "beak", "hex": "#e8943a"},
        {"id": "pouch", "hex": "#f2c14e"},
        {"id": "eye", "hex": "#1a1a1a"},
    ]
}
COLORWAYS = [
    {
        "id": "default",  # natural cream pelican on teal
        "mapping": {
            "ground": "#0e3b46", "body": "#f3ead7", "wing": "#5b7c99",
            "beak": "#e8943a", "pouch": "#f2c14e", "eye": "#1a1a1a",
        },
    },
    {
        "id": "dusk",  # SAME symbols, fully recolored (proves colorway-agnostic dedup)
        "mapping": {
            "ground": "#2a1a3a", "body": "#ffd9c0", "wing": "#7a5aa8",
            "beak": "#ff6f61", "pouch": "#ffb347", "eye": "#120016",
        },
    },
]

# Bind each motif slot (s0..s4, in draw order) to its palette slot.
COLORS = {"s0": "body", "s1": "wing", "s2": "beak", "s3": "pouch", "s4": "eye"}

TILE = 96.0


def build_intent(bird_id: str) -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": TILE, "dpi": 300},
        "seed": 7,
        "production": {"method": "digital", "max_colors": 12},
        "palette": PALETTE,
        "colorways": COLORWAYS,
        "layers": [
            {"id": "ground", "type": "background", "z_order": 0,
             "params": {"color": "ground"}},
            {
                "id": "pelicans",
                "type": "motif",
                "z_order": 1,
                "params": {"motif_id": bird_id, "size_mm": 34.0, "colors": COLORS},
                "placement": {
                    "type": "lattice",
                    "lattice": {
                        "cell_w_mm": 48, "cell_h_mm": 48,
                        "drop_fraction": 0.5, "drop_axis": "column",
                    },
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


def _render(svg, name, w, h, binary):
    with open(f"{OUT}/{name}.svg", "w") as f:
        f.write(svg)
    if binary:
        png, _ = rasterize(svg, "png", 200, w, h, binary=binary)
        Image.open(io.BytesIO(png)).convert("RGBA").save(f"{OUT}/{name}.png")


def main():
    motif = normalize_motif_svg(PELICAN_SVG)
    bird_id = register_motif(motif, subject="pelican", part="whole")
    print("registered pelican motif id:", bird_id)
    print("color_slots:", motif.color_slots, f"(N={len(motif.color_slots)})")
    print("per-slot render symbols:", [sid for sid, _ in slot_render_symbols(motif)])

    intent = build_intent(bird_id)

    # Validation must accept the full slot binding.
    validate_intent(intent)

    # Determinism + colorway-agnostic dedup checks (same as the regression tests).
    result = validate_intent(intent)
    svg_default = compose(result.intent, result.palette, "default")
    assert svg_default == compose(result.intent, result.palette, "default"), "non-deterministic"
    svg_dusk = compose(result.intent, result.palette, "dusk")

    def _symbols(svg):
        root = ET.fromstring(svg)
        return {s.get("id"): ET.tostring(s, encoding="unicode")
                for s in root.findall(f".//{NS}symbol")}

    assert _symbols(svg_default) == _symbols(svg_dusk), "symbols differ across colorway!"
    assert svg_default != svg_dusk, "colorway switch produced identical output"
    print("symbols:", sorted(_symbols(svg_default)))
    print("dedup OK: symbol defs byte-identical across 'default' and 'dusk' colorways")

    # Map slot -> resolved color per colorway (for the report).
    for cw in ("default", "dusk"):
        colors = {COLORS[s]: result.palette.resolve_color(COLORS[s], cw)
                  for s in motif.color_slots}
        print(f"  [{cw}] {colors}")

    binary = find_renderer("rsvg-convert") or find_renderer("resvg")

    # Single-tile + 2x2 tiled, both colorways.
    for cw, svg in (("default", svg_default), ("dusk", svg_dusk)):
        full = generate(intent, colorway_id=cw).svg  # full pipeline (seamless asserts)
        _render(full, f"pelican-{cw}-tile", TILE, TILE, binary)
        tiled, w, h = _tiled_svg(full, 2, 2)
        _render(tiled, f"pelican-{cw}-tiled", w, h, binary)

    print("renderer:", binary or "(none; PNG skipped)")
    print("output dir:", OUT)


if __name__ == "__main__":
    main()
