"""Placement strategy dispatch.

Maps a motif layer's ``placement.type`` to a strategy that produces instance
coordinates. Only ``path_following`` is implemented (session 3); ``lattice`` /
``point_set`` / ``scatter`` arrive in session 5.
"""

from __future__ import annotations

from app.engine.host import HostLayer
from app.engine.intent import MotifLayer
from app.engine.placement.path_following import Instance, place_path_following

__all__ = ["Instance", "place", "place_path_following"]


def place(layer: MotifLayer, host: HostLayer, tile_mm: float) -> list[Instance]:
    """Dispatch a motif layer's placement to its strategy."""
    placement = layer.placement
    if placement is None:
        raise ValueError(f"motif layer {layer.id!r} has no placement")
    if placement.type == "path_following":
        return place_path_following(host, placement, tile_mm)
    raise ValueError(f"unsupported placement type: {placement.type!r}")
