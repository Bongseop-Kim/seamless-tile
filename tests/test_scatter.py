"""Scatter placement: seed-deterministic blue-noise (poisson) and sateen step grid."""

import xml.etree.ElementTree as ET

import pytest

from app.engine.generate import generate
from app.engine.intent import Placement, ScatterSpec
from app.engine.placement.scatter import _torus_dist, place_scatter
from app.validate.intent import IntentInvalid, validate_intent

NS = "{http://www.w3.org/2000/svg}"
TILE = 48.0


def _scatter(**kwargs) -> Placement:
    return Placement(type="scatter", scatter=ScatterSpec(**kwargs))


# --- poisson (blue-noise) -----------------------------------------------------


def test_poisson_is_byte_deterministic_from_seed():
    p = _scatter(mode="poisson", min_dist_mm=8, count=6)
    assert place_scatter(p, TILE, seed=7) == place_scatter(p, TILE, seed=7)


def test_poisson_respects_torus_min_distance():
    p = _scatter(mode="poisson", min_dist_mm=8, count=6)
    inst = place_scatter(p, TILE, seed=7)
    for i in range(len(inst)):
        for j in range(i + 1, len(inst)):
            d = _torus_dist(inst[i].x_mm, inst[i].y_mm, inst[j].x_mm, inst[j].y_mm, TILE)
            assert d >= 8 - 1e-9


def test_poisson_respects_count_cap():
    p = _scatter(mode="poisson", min_dist_mm=6, count=5)
    inst = place_scatter(p, TILE, seed=3)
    assert 0 < len(inst) <= 5


# --- sateen (no row/column alignment) ----------------------------------------


def test_sateen_has_zero_alignment():
    inst = place_scatter(_scatter(mode="sateen", sateen_n=5, sateen_step=2), 50.0, seed=0)
    xs = [round(i.x_mm, 6) for i in inst]
    ys = [round(i.y_mm, 6) for i in inst]
    assert len(inst) == 5
    # zero alignment: every row and every column hosts exactly one point.
    assert len(set(xs)) == 5
    assert len(set(ys)) == 5


def test_sateen_is_deterministic():
    p = _scatter(mode="sateen", sateen_n=7, sateen_step=3)
    assert place_scatter(p, TILE, seed=0) == place_scatter(p, TILE, seed=0)


# --- validation ---------------------------------------------------------------


def _scatter_intent(scatter: dict) -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 1,
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
                "placement": {"type": "scatter", "scatter": scatter},
            },
        ],
    }


def test_poisson_requires_min_dist():
    with pytest.raises(IntentInvalid):
        validate_intent(_scatter_intent({"mode": "poisson"}))


def test_poisson_min_dist_capped_at_half_tile():
    with pytest.raises(IntentInvalid):
        validate_intent(_scatter_intent({"mode": "poisson", "min_dist_mm": 30}))


def test_sateen_step_must_be_coprime():
    with pytest.raises(IntentInvalid):
        validate_intent(
            _scatter_intent({"mode": "sateen", "sateen_n": 4, "sateen_step": 2})
        )


def test_scatter_generates_pattern_svg():
    cand = generate(_scatter_intent({"mode": "sateen", "sateen_n": 6, "sateen_step": 5}))
    root = ET.fromstring(cand.svg)
    assert len(root.findall(f".//{NS}pattern")) == 1
    assert len(root.findall(f".//{NS}use")) > 1
