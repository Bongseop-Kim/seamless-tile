"""Intent attribute showcase: one seamless pattern per major 'knob'.

Each entry isolates a single intent capability so you can see exactly what that
field does. All share one palette/canvas; only the highlighted attribute changes.

Covered:
  lattice   : block / half-drop / brick / drop_fraction=1/3   (placement.lattice)
  scatter   : poisson (blue-noise) / sateen                   (placement.scatter)
  symmetry  : mirror_2x2 / glide_h                            (intent.symmetry)

Directional motifs (bee) are used for symmetry so the reflection/glide is visible;
circles are used elsewhere so the placement itself is the only variable.

Usage:
    .venv/bin/python examples/intent-showcase/generate.py
"""

import io
import os
import sys
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as DefusedET

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402

from app.engine.generate import generate  # noqa: E402
from app.render.raster import find_renderer, rasterize  # noqa: E402
from app.render.svg import render_svg_document  # noqa: E402

OUT = _HERE
SVG_NS = "http://www.w3.org/2000/svg"
NS = f"{{{SVG_NS}}}"
ET.register_namespace("", SVG_NS)
TILE = 48.0
PALETTE = {"slots": [
    {"id": "ground", "hex": "#10243a"},
    {"id": "accent", "hex": "#ef8a7a"},
    {"id": "gold", "hex": "#f5ca57"},
]}
COLORWAYS = [{"id": "default", "mapping": {"ground": "#10243a", "accent": "#ef8a7a", "gold": "#f5ca57"}}]


def base(layers, symmetry=None):
    intent = {
        "intent_version": 1,
        "canvas": {"tile_mm": TILE, "dpi": 300},
        "seed": 7,
        "production": {"method": "digital", "max_colors": 12},
        "palette": PALETTE,
        "colorways": COLORWAYS,
        "layers": layers,
    }
    if symmetry:
        intent["symmetry"] = symmetry
    return intent


def bg():
    return {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "ground"}}


def motif(mid, color, size, placement, z=1):
    return {
        "id": f"{mid}_{z}", "type": "motif", "z_order": z,
        "params": {"motif_id": mid, "size_mm": size, "color": color},
        "placement": placement,
    }


def lattice(cell=12, drop_fraction=None, drop_axis="column"):
    spec = {"cell_w_mm": cell, "cell_h_mm": cell, "drop_axis": drop_axis}
    if drop_fraction is not None:
        spec["drop_fraction"] = drop_fraction
    return {"type": "lattice", "lattice": spec}


SHOWCASES = {
    # --- lattice modes (same 12mm cell; only the drop changes) -----------------
    "lattice-01-block": base([bg(), motif("circle", "accent", 5, lattice())]),
    "lattice-02-halfdrop": base([bg(), motif("circle", "accent", 5, lattice(drop_fraction=0.5, drop_axis="column"))]),
    "lattice-03-brick": base([bg(), motif("circle", "accent", 5, lattice(drop_fraction=0.5, drop_axis="row"))]),
    # cell 16 -> tile/cell = 3, so (tile/cell)*drop_fraction = 1 (integer) closes on the torus
    "lattice-04-drop-third": base([bg(), motif("circle", "accent", 6, lattice(cell=16, drop_fraction=1 / 3, drop_axis="column"))]),
    # --- scatter modes ---------------------------------------------------------
    "scatter-01-poisson": base([bg(), motif("circle", "accent", 3, {"type": "scatter", "scatter": {"mode": "poisson", "min_dist_mm": 7}})]),
    "scatter-02-sateen": base([bg(), motif("circle", "gold", 4, {"type": "scatter", "scatter": {"mode": "sateen", "sateen_n": 6, "sateen_step": 5}})]),
    # --- tile-level symmetry (directional bee so the reflection shows) ---------
    "symmetry-01-mirror2x2": base([bg(), motif("bee", "gold", 7, lattice(cell=24, drop_fraction=0.5))], symmetry={"kind": "mirror_2x2"}),
    "symmetry-02-glide-h": base([bg(), motif("bee", "gold", 7, lattice(cell=24, drop_fraction=0.5))], symmetry={"kind": "glide_h"}),
}


def _pattern_dims(svg):
    p = DefusedET.fromstring(svg).find(f".//{NS}pattern")
    if p is None:
        raise ValueError("SVG does not contain a pattern element")
    return float(p.get("width")), float(p.get("height"))


def _defs_inner_xml(defs_el):
    return "".join(
        ET.tostring(child, encoding="unicode")
        .replace(f' xmlns="{SVG_NS}"', "")
        .replace(" />", "/>")
        for child in list(defs_el)
    )


def _tiled_svg(svg, cols, rows):
    root = DefusedET.fromstring(svg)
    defs_el = root.find(f"{NS}defs")
    if defs_el is None:
        raise ValueError("SVG does not contain a defs element")
    defs = _defs_inner_xml(defs_el)
    p = defs_el.find(f".//{NS}pattern")
    if p is None:
        raise ValueError("SVG defs does not contain a pattern element")
    pw, ph = float(p.get("width")), float(p.get("height"))
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
            png, _ = rasterize(tiled, "png", 200, w, h, binary=binary)
            Image.open(io.BytesIO(png)).convert("RGBA").save(f"{OUT}/{name}-tiled.png")
        print(f"{name}: tile {_pattern_dims(svg)} -> 4x4 ({w:g}x{h:g}mm)" + ("  + PNG" if binary else ""))
    print("\nrenderer:", binary or "(none; PNGs skipped)")
    print("output dir:", OUT)


if __name__ == "__main__":
    main()
