"""Path-following placement: walk a host lane and drop instances along it.

This strategy depends ONLY on the ``HostLayer.lanes()`` contract (``LaneField`` +
``Centerline``), never on a primitive's internal geometry. Changing the host's
internal representation must not affect placement.

Instances are stepped by arc length from ``phase_mm`` in ``spacing_mm`` increments
over one torus closure period ``L = centerline.length_mm(tile_mm)``; coordinates
come back already wrapped onto the torus (``% tile_mm``). Reconciling
``spacing_mm | L`` exactly, dedup, and boundary clones are session-4 concerns.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.host import HostLayer, resolve_lane
from app.engine.intent import Placement

_EPS = 1e-9


@dataclass(frozen=True)
class Instance:
    """A placed motif instance in torus (mm) coordinates."""

    x_mm: float
    y_mm: float
    rotation_deg: float


def place_path_following(
    host: HostLayer, placement: Placement, tile_mm: float
) -> list[Instance]:
    """Generate instances along the host lane selected by ``placement``.

    ``spacing_mm``/``phase_mm`` are the along-lane step and offset taken from the
    ``Placement`` (not ``LaneField.spacing_mm``, which is the perpendicular band
    period). ``rotation: "follow_path"`` uses the lane tangent; otherwise 0.
    """
    if placement.lane is None:
        raise ValueError("path_following placement requires `lane`")
    if placement.spacing_mm is None:
        raise ValueError("path_following placement requires `spacing_mm`")

    lane = resolve_lane(host.lanes(), placement.lane)
    centerline = lane.centerline_path
    length = centerline.length_mm(tile_mm)
    spacing = placement.spacing_mm
    follow = placement.rotation == "follow_path"

    instances: list[Instance] = []
    k = 0
    while True:
        s = placement.phase_mm + k * spacing
        if s >= length - _EPS:
            break
        (x, y), tangent = centerline.point_at(s, tile_mm)
        instances.append(Instance(x, y, tangent if follow else 0.0))
        k += 1
    return instances
