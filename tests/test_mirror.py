"""Mirror/glide tile-level symmetry: super-tile baking, doubled dims, seam continuity."""

import io
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from app.engine.generate import generate
from app.render.raster import find_renderer, rasterize
from app.render.svg import render_svg_document
from app.validate.intent import IntentInvalid, validate_intent
from app.validate.seamless import TILING_SEAM_TOL, tiling_seam

NS = "{http://www.w3.org/2000/svg}"
TILE = 48.0


def mirror_intent(symmetry: dict | None) -> dict:
    intent = {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 11,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "ground", "hex": "#10243a"},
                {"id": "accent", "hex": "#ef8a7a"},
            ]
        },
        "colorways": [
            {"id": "default", "mapping": {"ground": "#10243a", "accent": "#ef8a7a"}}
        ],
        "layers": [
            {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "ground"}},
            {
                "id": "dots",
                "type": "motif",
                "z_order": 1,
                "params": {"motif_id": "circle", "size_mm": 3.0, "color": "accent"},
                # half-drop makes the content asymmetric so mirroring is meaningful.
                "placement": {
                    "type": "lattice",
                    "lattice": {
                        "cell_w_mm": 12,
                        "cell_h_mm": 12,
                        "drop_fraction": 0.5,
                        "drop_axis": "column",
                    },
                },
            },
        ],
    }
    if symmetry is not None:
        intent["symmetry"] = symmetry
    return intent


def _pattern(svg: str):
    return ET.fromstring(svg).find(f".//{NS}pattern")


def test_mirror_h_doubles_width():
    p = _pattern(generate(mirror_intent({"kind": "mirror_h"})).svg)
    assert p.get("width") == "96"
    assert p.get("height") == "48"


def test_mirror_v_doubles_height():
    p = _pattern(generate(mirror_intent({"kind": "mirror_v"})).svg)
    assert p.get("width") == "48"
    assert p.get("height") == "96"


def test_mirror_2x2_doubles_both():
    p = _pattern(generate(mirror_intent({"kind": "mirror_2x2"})).svg)
    assert p.get("width") == "96"
    assert p.get("height") == "96"


def test_mirror_keeps_single_pattern_not_enumerated():
    svg = generate(mirror_intent({"kind": "mirror_2x2"})).svg
    root = ET.fromstring(svg)
    assert len(root.findall(f".//{NS}pattern")) == 1
    assert len(root.findall(f".//{NS}circle")) == 1  # geometry defined once
    assert len(root.findall(f".//{NS}use")) > 1


def test_mirror_is_byte_deterministic():
    a = generate(mirror_intent({"kind": "mirror_2x2"})).svg
    b = generate(mirror_intent({"kind": "mirror_2x2"})).svg
    assert a == b


def test_glide_shift_must_divide_tile():
    with pytest.raises(IntentInvalid):
        validate_intent(mirror_intent({"kind": "glide_h", "shift_mm": 7}))


def test_glide_valid_shift_accepted():
    result = validate_intent(mirror_intent({"kind": "glide_h", "shift_mm": 24}))
    assert result.warnings == []


# --- raster seam guard (renderer-pinned, skip if absent) ---------------------


def _tiled_svg(single_svg: str, super_side: float, tiles: int) -> str:
    defs = single_svg[
        single_svg.index("<defs>") + len("<defs>") : single_svg.index("</defs>")
    ]
    side = tiles * super_side
    body = f'<rect x="0" y="0" width="{side}" height="{side}" fill="url(#tile)"/>'
    return render_svg_document(body, side, side, defs=defs)


def test_mirror_2x2_tiles_without_seam():
    binary = find_renderer("rsvg-convert")
    if binary is None:
        pytest.skip("rsvg-convert not available; raster seam guard skipped")
    super_side = 2 * TILE  # mirror_2x2 -> 96mm square super-tile
    svg = generate(mirror_intent({"kind": "mirror_2x2"})).svg
    tiled = _tiled_svg(svg, super_side, 2)
    png, _ = rasterize(tiled, "png", 300, 2 * super_side, binary=binary)
    arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
    tile_px = round(super_side / 25.4 * 300)
    excess_x, excess_y = tiling_seam(arr, tile_px)
    assert excess_x <= TILING_SEAM_TOL
    assert excess_y <= TILING_SEAM_TOL
