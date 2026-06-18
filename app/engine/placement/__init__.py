"""Placement strategy dispatch.

Maps a motif layer's ``placement.type`` to a strategy that produces instance
coordinates. ``path_following`` is host-based (session 3); ``lattice`` / ``point_set``
/ ``scatter`` (session 5) are host-free and laid out directly on the torus. ``scatter``
takes the intent ``seed`` so its blue-noise draw is reproducible.
"""

from __future__ import annotations

from app.engine.host import HostLayer
from app.engine.intent import MotifLayer
from app.engine.placement.lattice import place_lattice
from app.engine.placement.path_following import Instance, place_path_following
from app.engine.placement.point_set import place_point_set
from app.engine.placement.scatter import place_scatter

__all__ = [
    "Instance",
    "place",
    "place_path_following",
    "place_lattice",
    "place_scatter",
    "place_point_set",
]


def place(
    layer: MotifLayer, host: HostLayer | None, tile_mm: float, seed: int
) -> list[Instance]:
    """Dispatch a motif layer's placement to its strategy."""
    placement = layer.placement
    if placement is None:
        raise ValueError(f"motif layer {layer.id!r} has no placement")
    if placement.type == "path_following":
        # host may be None for a standalone path (host-free wave/straight lane).
        return place_path_following(host, placement, tile_mm)
    if placement.type == "lattice":
        return place_lattice(placement, tile_mm)
    if placement.type == "scatter":
        return place_scatter(placement, tile_mm, seed)
    if placement.type == "point_set":
        return place_point_set(placement, tile_mm)
    raise ValueError(f"unsupported placement type: {placement.type!r}")
