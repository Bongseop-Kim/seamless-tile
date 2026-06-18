"""Shared seamless guarantees: commensurability assertions and boundary clones.

Structural seamlessness is guaranteed *by construction*, not by pixel correction:

- Commensurability is enforced upstream by ``validate_intent`` (``period|tile``,
  ``spacing|tile``) and re-asserted here as a by-construction guard at the
  generate boundary (``assert_seamless_invariants``).
- Placement coordinates are already torus-wrapped (``% tile``) by
  ``Centerline.point_at``; this module adds **boundary clones** so a motif whose
  rendered box straddles a tile edge reappears on the opposite edge. A clone is
  just another ``<use>`` of the same ``<symbol>`` (no geometry duplication); the
  ``<pattern>`` clips the overflow.

NOTE: ``spacing | tile`` does not imply ``spacing | L`` where ``L`` is the lane
closure length ``tile*hypot(p, q)``. The resulting interior even-spacing glitch
near closure is a session-5 concern; it is not a tile-boundary seam and is
intentionally NOT enforced here (enforcing it would reject valid MVP intents).
"""

from __future__ import annotations

import math

from app.engine.intent import Intent
from app.engine.placement import Instance
from app.engine.units import divides, snap_angle, stripe_tiles
from app.motifs.registry import MotifDef

_EPS = 1e-9
_OFFSETS = (-1, 0, 1)  # fixed iteration order -> deterministic clone ordering


def _rendered_aabb(
    motif: MotifDef, inst: Instance, size_mm: float
) -> tuple[float, float, float, float]:
    """Axis-aligned bbox of the instance after scale -> rotate(about anchor) ->
    translate, matching ``composition._instance_transform``'s transform order."""
    min_x, min_y, max_x, max_y = motif.bbox_mm
    extent = max(max_x - min_x, max_y - min_y)
    scale = size_mm / extent
    ax, ay = motif.anchor
    theta = math.radians(inst.rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    xs: list[float] = []
    ys: list[float] = []
    for cx, cy in ((min_x, min_y), (min_x, max_y), (max_x, min_y), (max_x, max_y)):
        sx, sy = (cx - ax) * scale, (cy - ay) * scale
        rx = sx * cos_t - sy * sin_t
        ry = sx * sin_t + sy * cos_t
        xs.append(inst.x_mm + rx)
        ys.append(inst.y_mm + ry)
    return (min(xs), min(ys), max(xs), max(ys))


def clone_instances(
    instances: list[Instance],
    *,
    motif: MotifDef,
    size_mm: float,
    tile_mm: float,
) -> list[Instance]:
    """Return ``instances`` plus torus boundary clones. Pure and deterministic.

    For each instance whose rendered AABB crosses a tile edge, append shifted
    copies at ``(dx*tile, dy*tile)`` for ``dx, dy in {-1, 0, 1}`` (excluding
    ``(0, 0)``) whose shifted AABB still intersects ``[0, tile]^2`` (at most three
    clones; a corner crosser yields four copies total). Clones keep ``rotation_deg``.

    Assumes ``size_mm <= tile_mm``: clones are emitted in the same layer group as
    the original, so a motif larger than the tile could overlap its own clone and
    double-blend under layer opacity < 1.0. The MVP motifs are far smaller than the tile.
    """
    out: list[Instance] = []
    for inst in instances:
        out.append(inst)
        min_x, min_y, max_x, max_y = _rendered_aabb(motif, inst, size_mm)
        crosses = (
            min_x < -_EPS
            or min_y < -_EPS
            or max_x > tile_mm + _EPS
            or max_y > tile_mm + _EPS
        )
        if not crosses:
            continue
        for dx in _OFFSETS:
            for dy in _OFFSETS:
                if dx == 0 and dy == 0:
                    continue
                shifted_min_x = min_x + dx * tile_mm
                shifted_min_y = min_y + dy * tile_mm
                shifted_max_x = max_x + dx * tile_mm
                shifted_max_y = max_y + dy * tile_mm
                outside = (
                    shifted_max_x < -_EPS
                    or shifted_max_y < -_EPS
                    or shifted_min_x > tile_mm + _EPS
                    or shifted_min_y > tile_mm + _EPS
                )
                if outside:
                    continue
                out.append(
                    Instance(
                        inst.x_mm + dx * tile_mm,
                        inst.y_mm + dy * tile_mm,
                        inst.rotation_deg,
                    )
                )
    return out


def assert_seamless_invariants(intent: Intent) -> None:
    """By-construction guard at the generate boundary.

    Re-asserts the commensurability already enforced by ``validate_intent``
    (``period|tile`` for stripes, ``spacing|tile`` for placements) and that each
    stripe's snapped angle has an integer rational closure ``(p, q)``. Raises
    ``AssertionError`` on violation. Does NOT enforce ``spacing|L`` (see module docstring).
    """
    tile = intent.canvas.tile_mm
    for layer in intent.layers:
        if layer.type == "stripe":
            period = layer.params.period_mm
            snapped = snap_angle(layer.params.angle, tile, period)
            if not stripe_tiles(tile, period, snapped.p, snapped.q):
                raise AssertionError(
                    f"layer {layer.id!r}: stripe (angle {layer.params.angle}, period {period}) "
                    f"is not tile-commensurate (snapped slope {snapped.p}/{snapped.q}); "
                    f"requires tile_mm == k*period_mm*hypot(p, q)"
                )
        elif layer.type == "motif":
            placement = layer.placement
            if placement is not None and placement.spacing_mm is not None:
                if not divides(tile, placement.spacing_mm):
                    raise AssertionError(
                        f"layer {layer.id!r}: spacing_mm {placement.spacing_mm} "
                        f"does not divide tile_mm {tile}"
                    )
