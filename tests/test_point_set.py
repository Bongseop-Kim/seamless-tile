"""Point-set placement: explicit anchor points wrapped onto the torus."""

import pytest

from app.engine.intent import PointSetSpec, Placement
from app.engine.placement.point_set import place_point_set
from app.validate.intent import IntentInvalid, validate_intent

TILE = 48.0


def _point_set(points) -> Placement:
    return Placement(type="point_set", point_set=PointSetSpec(points=points))


def test_points_become_instances():
    inst = place_point_set(_point_set([(0.0, 0.0), (12.0, 36.0)]), TILE)
    assert [(i.x_mm, i.y_mm) for i in inst] == [(0.0, 0.0), (12.0, 36.0)]


def test_points_are_torus_wrapped():
    inst = place_point_set(_point_set([(50.0, 49.0)]), TILE)
    assert inst[0].x_mm == pytest.approx(2.0)
    assert inst[0].y_mm == pytest.approx(1.0)


def _point_set_intent(points) -> dict:
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
                "placement": {"type": "point_set", "point_set": {"points": points}},
            },
        ],
    }


def test_points_outside_tile_rejected():
    with pytest.raises(IntentInvalid):
        validate_intent(_point_set_intent([[60.0, 10.0]]))


def test_valid_point_set_intent_accepted():
    result = validate_intent(_point_set_intent([[6.0, 6.0], [24.0, 24.0]]))
    assert result.warnings == []
