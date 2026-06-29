"""Lattice placement: block/half_drop/brick absorption + drop_fraction, and a full
non-diagonal all-over intent (overfit counter-proof: same engine, non-diagonal series)."""

import io
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as DefusedET
import pytest
from PIL import Image

from app.engine.generate import generate
from app.engine.intent import LatticeSpec, Placement
from app.engine.placement.lattice import place_lattice
from app.engine.seamless import assert_seamless_invariants
from app.render.raster import find_renderer, rasterize
from app.render.svg import render_svg_document
from app.validate.intent import IntentInvalid, validate_intent
from app.validate.seamless import TILING_SEAM_TOL, tiling_seam

NS = "{http://www.w3.org/2000/svg}"
TILE = 48.0


def _pts(instances):
    return sorted((round(i.x_mm, 6), round(i.y_mm, 6)) for i in instances)


def _lattice(**kwargs) -> Placement:
    return Placement(type="lattice", lattice=LatticeSpec(**kwargs))


def allover_lattice_intent(drop_fraction=0.5, drop_axis="column") -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 7,
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
                "params": {"motif_id": "circle", "size_mm": 2.0, "color": "accent"},
                "placement": {
                    "type": "lattice",
                    "lattice": {
                        "cell_w_mm": 12,
                        "cell_h_mm": 12,
                        "drop_fraction": drop_fraction,
                        "drop_axis": drop_axis,
                    },
                },
            },
        ],
    }


def test_lattice_instance_budget_rejected_in_validate():
    """A1: a tiny cell that still divides the tile blows up instance count -> 422."""
    intent = allover_lattice_intent()
    intent["layers"][1]["placement"]["lattice"]["cell_w_mm"] = 0.1
    intent["layers"][1]["placement"]["lattice"]["cell_h_mm"] = 0.1  # 480*480 = 230400
    with pytest.raises(IntentInvalid) as exc:
        validate_intent(intent)
    assert "max_placement_instances" in str(exc.value)


def test_place_lattice_rejects_excessive_instance_count():
    """A1: defensive guard for direct engine callers that bypass validate_intent."""
    with pytest.raises(ValueError):
        place_lattice(_lattice(cell_w_mm=0.1, cell_h_mm=0.1), TILE)


# --- geometry: absorbs repeat.py block/half_drop/brick ------------------------


def test_block_fills_tile_grid():
    inst = place_lattice(_lattice(cell_w_mm=24, cell_h_mm=24), TILE)
    assert _pts(inst) == [(0.0, 0.0), (0.0, 24.0), (24.0, 0.0), (24.0, 24.0)]


def test_half_drop_column_offsets_alternating_columns():
    inst = place_lattice(
        _lattice(cell_w_mm=24, cell_h_mm=24, drop_fraction=0.5, drop_axis="column"), TILE
    )
    # column 1 is dropped by drop_fraction*cell_h = 12mm vs column 0.
    assert _pts(inst) == [(0.0, 0.0), (0.0, 24.0), (24.0, 12.0), (24.0, 36.0)]


def test_brick_row_offsets_alternating_rows():
    inst = place_lattice(
        _lattice(cell_w_mm=24, cell_h_mm=24, drop_fraction=0.5, drop_axis="row"), TILE
    )
    # row 1 is shifted by drop_fraction*cell_w = 12mm vs row 0.
    assert _pts(inst) == [(0.0, 0.0), (12.0, 24.0), (24.0, 0.0), (36.0, 24.0)]


def test_drop_fraction_third():
    inst = place_lattice(
        _lattice(cell_w_mm=16, cell_h_mm=24, drop_fraction=1 / 3, drop_axis="column"), TILE
    )
    assert _pts(inst) == [
        (0.0, 0.0),
        (0.0, 24.0),
        (16.0, 8.0),
        (16.0, 32.0),
        (32.0, 16.0),
        (32.0, 40.0),
    ]


def test_drop_fraction_quarter_count():
    inst = place_lattice(
        _lattice(cell_w_mm=12, cell_h_mm=24, drop_fraction=0.25, drop_axis="column"), TILE
    )
    assert len(inst) == 8  # nx=4 * ny=2


def test_lattice_is_deterministic():
    spec = _lattice(cell_w_mm=12, cell_h_mm=12, drop_fraction=0.5)
    assert place_lattice(spec, TILE) == place_lattice(spec, TILE)


# --- validation ---------------------------------------------------------------


def test_cell_must_divide_tile():
    raw = allover_lattice_intent()
    raw["layers"][1]["placement"]["lattice"]["cell_w_mm"] = 7
    with pytest.raises(IntentInvalid):
        validate_intent(raw)


def test_drop_must_close_on_torus():
    raw = allover_lattice_intent()
    raw["layers"][1]["placement"]["lattice"] = {
        "cell_w_mm": 24,  # nx = 2; nx*1/3 not integer -> no closure
        "cell_h_mm": 24,
        "drop_fraction": 1 / 3,
        "drop_axis": "column",
    }
    with pytest.raises(IntentInvalid):
        validate_intent(raw)


def test_disallowed_drop_fraction_rejected():
    raw = allover_lattice_intent(drop_fraction=0.37)
    with pytest.raises(IntentInvalid):
        validate_intent(raw)


# --- full non-diagonal all-over intent ---------------------------------------


def test_allover_lattice_passes_invariants():
    result = validate_intent(allover_lattice_intent())
    assert_seamless_invariants(result.intent)  # must not raise


def test_allover_lattice_generates_pattern_svg_not_enumerated():
    cand = generate(allover_lattice_intent())
    root = ET.fromstring(cand.svg)
    assert len(root.findall(f".//{NS}pattern")) == 1
    circles = root.findall(f".//{NS}circle")
    uses = root.findall(f".//{NS}use")
    assert len(circles) == 1  # geometry defined once
    assert len(uses) > 1  # instanced via <use>, not enumerated


def test_allover_lattice_is_byte_deterministic():
    a = generate(allover_lattice_intent())
    b = generate(allover_lattice_intent())
    assert a.svg == b.svg


# --- raster seam guard (renderer-pinned, skip if absent) ---------------------


def _tiled_svg(single_svg: str, tiles: int) -> str:
    root = DefusedET.fromstring(single_svg)
    defs_el = root.find(f"{NS}defs")
    defs = (
        "".join(ET.tostring(child, encoding="unicode") for child in list(defs_el))
        if defs_el is not None
        else ""
    )
    side = tiles * TILE
    body = f'<rect x="0" y="0" width="{side}" height="{side}" fill="url(#tile)"/>'
    return render_svg_document(body, side, side, defs=defs)


def test_allover_lattice_tiles_without_seam():
    binary = find_renderer("rsvg-convert")
    if binary is None:
        pytest.skip("rsvg-convert not available; raster seam guard skipped")
    svg = generate(allover_lattice_intent()).svg
    tiled = _tiled_svg(svg, 2)
    png, _ = rasterize(tiled, "png", 300, 2 * TILE, binary=binary)
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    tile_px = round(TILE / 25.4 * 300)
    excess_x, excess_y = tiling_seam(image, tile_px)
    assert excess_x <= TILING_SEAM_TOL
    assert excess_y <= TILING_SEAM_TOL
