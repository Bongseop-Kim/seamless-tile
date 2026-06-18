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

from app.engine.host import Centerline, HostLayer, resolve_lane
from app.engine.intent import PathSpec, Placement
from app.engine.units import snap_angle, snap_spacing

_EPS = 1e-9


def _centerline_from_path(path: PathSpec, tile_mm: float) -> Centerline:
    """Build a standalone (host-free) centerline from a ``PathSpec``.

    The angle is snapped to a tile-commensurate slope (square tile: slope depends only
    on the angle and the denominator cap), so the lane closes on the torus.
    """
    if path.kind == "custom":
        raise ValueError("path_following: custom path_id is out of session-5 scope")
    angle = path.angle if path.angle is not None else 0.0
    snapped = snap_angle(angle, tile_mm, tile_mm)
    if path.kind == "wave":
        if path.wavelength is None or path.amplitude is None:
            raise ValueError("wave path requires wavelength and amplitude")
        return Centerline(
            angle_deg=snapped.angle_deg,
            offset_mm=0.0,
            p=snapped.p,
            q=snapped.q,
            kind="wave",
            wavelength_mm=path.wavelength,
            amplitude_mm=path.amplitude,
        )
    return Centerline(
        angle_deg=snapped.angle_deg, offset_mm=0.0, p=snapped.p, q=snapped.q
    )


def _resolve_centerline(
    host: HostLayer | None, placement: Placement, tile_mm: float
) -> Centerline:
    # Standalone path takes precedence when no host lane is referenced.
    if placement.path is not None and placement.host_layer is None:
        return _centerline_from_path(placement.path, tile_mm)
    if placement.lane is None:
        raise ValueError(
            "path_following placement requires `lane` (or a standalone `path`)"
        )
    if host is None:
        raise ValueError("path_following placement requires a host layer for `lane`")
    return resolve_lane(host.lanes(), placement.lane).centerline_path


@dataclass(frozen=True)
class Instance:
    """A placed motif instance in torus (mm) coordinates."""

    x_mm: float
    y_mm: float
    rotation_deg: float


def place_path_following(
    host: HostLayer | None, placement: Placement, tile_mm: float
) -> list[Instance]:
    """Generate instances along the selected lane (host-based or standalone path).

    ``spacing_mm``/``phase_mm`` are the along-lane step and offset taken from the
    ``Placement`` (not ``LaneField.spacing_mm``, which is the perpendicular band
    period). ``rotation: "follow_path"`` uses the lane tangent; otherwise 0. A curved
    (``wave``) lane reuses this exact loop -- only ``Centerline.point_at`` differs.
    """
    if placement.spacing_mm is None:
        raise ValueError("path_following placement requires `spacing_mm`")

    centerline = _resolve_centerline(host, placement, tile_mm)
    length = centerline.length_mm(tile_mm)
    # Snap the requested step to an exact divisor of the closure length so the rhythm is
    # uniform across the torus wrap (diagonal lanes rarely divide `length` evenly).
    _, spacing = snap_spacing(length, placement.spacing_mm)
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
