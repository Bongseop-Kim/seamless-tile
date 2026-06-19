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

from app.engine.intent import Intent, SymmetrySpec
from app.engine.placement import Instance
from app.engine.units import divides, fmt, snap_angle, stripe_tiles
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
    (``period|tile`` for stripes, ``spacing|tile`` for path placements, lattice cell
    divisibility, sateen coprimality) and that each stripe's snapped angle has an
    integer rational closure ``(p, q)``. Raises ``AssertionError`` on violation. Does
    NOT enforce ``spacing|L`` (see module docstring).
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
            if layer.params.size_mm > tile:
                raise AssertionError(
                    f"layer {layer.id!r}: motif size_mm {layer.params.size_mm} exceeds "
                    f"tile_mm {tile} (boundary clones would self-overlap)"
                )
            placement = layer.placement
            if placement is None:
                continue
            if (
                placement.path is not None
                and placement.path.kind == "wave"
                and placement.path.wavelength is not None
            ):
                angle = placement.path.angle if placement.path.angle is not None else 0.0
                snapped = snap_angle(angle, tile, tile)
                closure = tile * math.hypot(snapped.p, snapped.q)
                if not divides(closure, placement.path.wavelength):
                    raise AssertionError(
                        f"layer {layer.id!r}: wave wavelength {placement.path.wavelength} "
                        f"does not divide the lane closure length {closure}"
                    )
            if placement.type == "lattice" and placement.lattice is not None:
                spec = placement.lattice
                if not (divides(tile, spec.cell_w_mm) and divides(tile, spec.cell_h_mm)):
                    raise AssertionError(
                        f"layer {layer.id!r}: lattice cell "
                        f"({spec.cell_w_mm}, {spec.cell_h_mm}) does not divide tile_mm {tile}"
                    )
            if placement.type == "scatter" and placement.scatter is not None:
                spec = placement.scatter
                if spec.mode == "sateen" and spec.sateen_n is not None:
                    step = spec.sateen_step if spec.sateen_step is not None else 1
                    if math.gcd(step, spec.sateen_n) != 1:
                        raise AssertionError(
                            f"layer {layer.id!r}: sateen_step {step} is not coprime "
                            f"with sateen_n {spec.sateen_n}"
                        )


def _reflect_group(content: str, tx: float, ty: float, sx: int, sy: int) -> str:
    """Wrap ``content`` in a reflected/translated group (a single <g>, no geometry copy).

    The same ``<use>`` elements are re-referenced inside the group, so the
    enumerate-free invariant holds; the ``<pattern>`` clips overflow.
    """
    return (
        f'<g transform="translate({fmt(tx)} {fmt(ty)}) scale({sx} {sy})">'
        f"{content}</g>"
    )


def super_tile(
    content: str, tile_mm: float, symmetry: SymmetrySpec
) -> tuple[str, float, float]:
    """Bake a tile-level mirror/glide symmetry into a super-tile.

    Returns ``(super_content, width_mm, height_mm)``. The base tile occupies
    ``[0, tile)^2``; reflected copies fill the rest of the (doubled) super-tile, which
    then block-tiles seamlessly. Mirror seam continuity is by construction: the base is
    already torus-seamless, and a reflection meeting the base at the internal axis is
    continuous, while the super-tile's outer edges (reflection-invariant boundary
    columns/rows) match under block tiling.
    """
    t = tile_mm
    kind = symmetry.kind
    if kind == "mirror_h":
        return content + _reflect_group(content, 2 * t, 0.0, -1, 1), 2 * t, t
    if kind == "mirror_v":
        return content + _reflect_group(content, 0.0, 2 * t, 1, -1), t, 2 * t
    if kind == "mirror_2x2":
        h = _reflect_group(content, 2 * t, 0.0, -1, 1)
        v = _reflect_group(content, 0.0, 2 * t, 1, -1)
        hv = _reflect_group(content, 2 * t, 2 * t, -1, -1)
        return content + h + v + hv, 2 * t, 2 * t
    if kind in ("glide_h", "glide_v"):
        shift = symmetry.shift_mm if symmetry.shift_mm is not None else t / 2.0
        if kind == "glide_h":
            # Reflected half is shifted along y; emit twice (shift, shift-tile) so the
            # pattern box is fully covered (a single shifted group would leave a gap).
            g1 = _reflect_group(content, 2 * t, shift, -1, 1)
            g2 = _reflect_group(content, 2 * t, shift - t, -1, 1)
            return content + g1 + g2, 2 * t, t
        g1 = _reflect_group(content, shift, 2 * t, 1, -1)
        g2 = _reflect_group(content, shift - t, 2 * t, 1, -1)
        return content + g1 + g2, t, 2 * t
    raise ValueError(f"unsupported symmetry kind: {kind!r}")
