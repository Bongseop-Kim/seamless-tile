"""Point-set placement: instances at explicit anchor points (mm).

The simplest strategy -- the caller supplies the anchors directly (e.g. computed
lattice intersections). Coordinates are wrapped onto the torus (``% tile``); validity
of the points is checked upstream by ``validate_intent``.
"""

from __future__ import annotations

from app.engine.intent import Placement
from app.engine.placement.path_following import Instance


def place_point_set(placement: Placement, tile_mm: float) -> list[Instance]:
    spec = placement.point_set
    if spec is None:
        raise ValueError("point_set placement requires a `point_set` spec")
    return [Instance(x % tile_mm, y % tile_mm, 0.0) for x, y in spec.points]
