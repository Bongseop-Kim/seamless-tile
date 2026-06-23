import io
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from app.engine.generate import generate
from app.engine.intent import Intent
from app.engine.placement import Instance
from app.engine.seamless import assert_seamless_invariants, clone_instances
from app.motifs.registry import get_motif
from app.render.raster import find_renderer, rasterize
from app.render.svg import render_svg_document
from app.validate.intent import IntentInvalid, validate_intent
from app.validate.seamless import TILING_SEAM_TOL, tiling_seam
from tests.test_intent import mvp_intent

NS = "{http://www.w3.org/2000/svg}"
TILE = 48.0
CIRCLE = get_motif("circle")
BEE = get_motif("bee")


def _clones(instances, motif, size_mm):
    out = clone_instances(instances, motif=motif, size_mm=size_mm, tile_mm=TILE)
    return out, len(out) - len(instances)


# --- by-construction invariants ---------------------------------------------


def test_assert_invariants_pass_for_mvp():
    result = validate_intent(mvp_intent())
    assert_seamless_invariants(result.intent)  # must not raise


def test_assert_invariants_reject_non_tiling_diagonal():
    raw = mvp_intent()
    raw["layers"][1]["params"] = {
        "angle": -32,  # snaps to 5/8 (irrational hypot) -> never tiles
        "period_mm": 24,
        "bands": [{"offset_mm": 6, "width_mm": 12, "color": "accent"}],
    }
    # Bypass validate_intent (which would also reject) to exercise the guard directly.
    intent = Intent.model_validate(raw)
    with pytest.raises(AssertionError):
        assert_seamless_invariants(intent)


def test_assert_invariants_reject_motif_larger_than_tile():
    """A7: by-construction guard rejects size_mm > tile_mm (clone self-overlap)."""
    raw = mvp_intent()
    raw["layers"][2]["params"]["size_mm"] = 60.0  # tile_mm is 48
    intent = Intent.model_validate(raw)  # bypass validate_intent (which also rejects)
    with pytest.raises(AssertionError):
        assert_seamless_invariants(intent)


def test_validate_rejects_non_tiling_diagonal():
    raw = mvp_intent()
    raw["layers"][1]["params"]["angle"] = -32
    raw["layers"][1]["params"]["period_mm"] = 24
    # repair=True snaps off-grid periods; the rejection still holds when repair is off.
    with pytest.raises(IntentInvalid):
        validate_intent(raw, repair=False)


def test_tiling_seam_rejects_invalid_bounds():
    arr = np.zeros((10, 10, 4), dtype=np.uint8)
    with pytest.raises(ValueError, match="margin"):
        tiling_seam(arr, tile_px=5, margin=-1)
    with pytest.raises(ValueError, match="tile_px"):
        tiling_seam(arr, tile_px=9, margin=2)


# --- boundary clone ----------------------------------------------------------


def test_corner_crosser_gets_three_clones():
    out, n = _clones([Instance(0.0, 0.0, 0.0)], CIRCLE, 1.0)
    assert n == 3
    clone_xy = {(round(i.x_mm, 6), round(i.y_mm, 6)) for i in out[1:]}
    assert clone_xy == {(TILE, 0.0), (0.0, TILE), (TILE, TILE)}


def test_single_edge_crosser_gets_one_clone():
    out, n = _clones([Instance(47.8, 24.0, 0.0)], CIRCLE, 1.0)
    assert n == 1
    assert out[1].x_mm == pytest.approx(47.8 - TILE)
    assert out[1].y_mm == pytest.approx(24.0)


def test_fully_interior_gets_no_clone():
    _, n = _clones([Instance(24.0, 24.0, 0.0)], CIRCLE, 1.0)
    assert n == 0


def test_touching_edge_is_not_a_crosser():
    # AABB max_x == tile exactly -> not crossing (a zero-width clip is invisible).
    _, n = _clones([Instance(47.5, 24.0, 0.0)], CIRCLE, 1.0)
    assert n == 0


def test_rotation_is_applied_to_crossing_test():
    # A 5mm bee at x=45 is interior unrotated (AABB max 47.5) but its 45deg-rotated
    # AABB (half-extent 2.5*sqrt2 ~ 3.54) crosses the right edge.
    _, n0 = _clones([Instance(45.0, 24.0, 0.0)], BEE, 5.0)
    _, n45 = _clones([Instance(45.0, 24.0, 45.0)], BEE, 5.0)
    assert n0 == 0
    assert n45 >= 1


# --- output topology (post-clone) -------------------------------------------


def test_generate_output_is_pattern_based_not_enumerated():
    svg = generate(mvp_intent()).svg
    root = ET.fromstring(svg)
    assert len(root.findall(f".//{NS}pattern")) == 1
    symbol_ids = sorted(s.get("id") for s in root.findall(f".//{NS}symbol"))
    assert symbol_ids == ["motif-bee", "motif-circle"]
    circles = root.findall(f".//{NS}circle")
    ellipses = root.findall(f".//{NS}ellipse")
    uses = root.findall(f".//{NS}use")
    assert len(circles) == 1  # circle geometry defined once
    assert len(ellipses) == 3  # bee geometry defined once
    assert len(uses) > len(circles) + len(ellipses)


# --- raster seam regression guard (renderer-pinned, skip if absent) ---------


def _tiled_svg(single_tile_svg: str, tiles: int) -> str:
    defs = single_tile_svg[
        single_tile_svg.index("<defs>") + len("<defs>") : single_tile_svg.index("</defs>")
    ]
    side = tiles * TILE
    body = f'<rect x="0" y="0" width="{side}" height="{side}" fill="url(#tile)"/>'
    return render_svg_document(body, side, side, defs=defs)


def test_mvp_tiles_without_seam():
    binary = find_renderer("rsvg-convert")
    if binary is None:
        pytest.skip("rsvg-convert not available; raster seam guard skipped")
    svg = generate(mvp_intent()).svg
    tiled = _tiled_svg(svg, 2)
    png, _ = rasterize(tiled, "png", 300, 2 * TILE, binary=binary)
    arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
    tile_px = round(TILE / 25.4 * 300)
    excess_x, excess_y = tiling_seam(arr, tile_px)
    assert excess_x <= TILING_SEAM_TOL
    assert excess_y <= TILING_SEAM_TOL


def _object_repeat_ground(motif_id: str) -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": TILE, "dpi": 300},
        "seed": 0,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {
            "slots": [{"id": "ground", "hex": "#10243a"}, {"id": "tone", "hex": "#1b3450"}]
        },
        "colorways": [
            {
                "id": "default",
                "name": "default",
                "mapping": {"ground": "#10243a", "tone": "#1b3450"},
            }
        ],
        "layers": [
            {
                "id": "ground",
                "type": "background",
                "z_order": 0,
                "params": {
                    "color": "ground",
                    "kind": "object_repeat",
                    "motif_id": motif_id,
                    "cell_mm": 8,  # divides TILE 48
                    "texture_color": "tone",
                },
            }
        ],
    }


@pytest.mark.parametrize("motif_id", ["twill", "herringbone", "diamond"])
def test_object_repeat_ground_tiles_without_seam(motif_id):
    # The typed object_repeat ground bakes its texture lattice in; it must tile seamlessly
    # on its own (line weaves included -- they read as clean full-field fabric as a ground).
    binary = find_renderer("rsvg-convert")
    if binary is None:
        pytest.skip("rsvg-convert not available; raster seam guard skipped")
    intent = _object_repeat_ground(motif_id)
    assert_seamless_invariants(validate_intent(intent).intent)  # by-construction
    tiled = _tiled_svg(generate(intent).svg, 2)
    png, _ = rasterize(tiled, "png", 300, 2 * TILE, binary=binary)
    arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
    tile_px = round(TILE / 25.4 * 300)
    excess_x, excess_y = tiling_seam(arr, tile_px)
    assert excess_x <= TILING_SEAM_TOL
    assert excess_y <= TILING_SEAM_TOL


def test_validate_rejects_object_repeat_non_dividing_cell():
    intent = _object_repeat_ground("twill")
    intent["layers"][0]["params"]["cell_mm"] = 7  # does not divide TILE 48
    with pytest.raises(IntentInvalid):
        validate_intent(intent)
