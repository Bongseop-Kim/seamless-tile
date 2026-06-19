"""Stage-0 intent validation and repair (structural + semantic).

Runs between the LLM/image adapters and the engine. Structural validation is
delegated to the pydantic ``Intent`` model; this module adds cross-field semantic
checks, a single non-blocking gamut warning pass, and one round of safe-value
repair (currently: dpi clamping).

Commensurability / angle-snap rules follow the single-source policy in
``docs/plan/00-overview.md``. Session 1 validates structure, divisibility
(period | tile, spacing | tile, wave wavelength | tile) and references. A
commensurate diagonal always exists for a positive tile, so the actual angle snap
and its reported deviation are deferred to session 2's ``snap_angle``.
"""

import math
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.config import ALLOWED_DPI
from app.engine.intent import Intent
from app.engine.palette import ColorSlot, Colorway, Palette, out_of_gamut
from app.engine.units import divides, snap_angle, snap_spacing, stripe_tiles
from app.motifs.registry import get_motif


class IntentInvalid(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass
class ValidationResult:
    intent: Intent
    palette: Palette
    warnings: list[str] = field(default_factory=list)


def _fmt_err(err: dict) -> str:
    loc = ".".join(str(p) for p in err.get("loc", ()))
    return f"{loc}: {err.get('msg', 'invalid')}" if loc else err.get("msg", "invalid")


def _layer_slot_refs(layer) -> list[str]:
    if layer.type == "background":
        return [layer.params.color]
    if layer.type == "stripe":
        return [b.color for b in layer.params.bands]
    if layer.type == "motif":
        if layer.params.colors:
            return list(layer.params.colors.values())
        if layer.params.color is not None:
            return [layer.params.color]
    return []


def build_palette(intent: Intent) -> Palette:
    slots = tuple(
        ColorSlot(id=s.id, hex=s.hex, spot=s.spot, name=s.name)
        for s in intent.palette.slots
    )
    colorways = tuple(
        Colorway(id=c.id, name=c.name, mapping=dict(c.mapping))
        for c in intent.colorways
    )
    return Palette(slots=slots, colorways=colorways)


_ALLOWED_DROP_FRACTIONS = (0.5, 1 / 3, 0.25)


def _approx_int(value: float, tol: float = 1e-6) -> bool:
    return abs(value - round(value)) <= tol


def _lattice_errors(layer, placement, tile: float) -> list[str]:
    spec = placement.lattice
    if spec is None:
        return [f"layer {layer.id!r}: lattice placement requires a `lattice` spec"]
    errs: list[str] = []
    if not divides(tile, spec.cell_w_mm):
        errs.append(
            f"layer {layer.id!r}: lattice cell_w_mm {spec.cell_w_mm} does not divide "
            f"tile_mm {tile}"
        )
    if not divides(tile, spec.cell_h_mm):
        errs.append(
            f"layer {layer.id!r}: lattice cell_h_mm {spec.cell_h_mm} does not divide "
            f"tile_mm {tile}"
        )
    if spec.drop_fraction is not None:
        if not any(abs(spec.drop_fraction - f) <= 1e-6 for f in _ALLOWED_DROP_FRACTIONS):
            errs.append(
                f"layer {layer.id!r}: lattice drop_fraction {spec.drop_fraction} must "
                f"be one of 1/2, 1/3, 1/4"
            )
        elif not errs:
            # Torus closure: (tile/cell)*drop must be an integer along the drop axis,
            # else the sheared lattice does not contain the (tile, 0)/(0, tile) wraps.
            counts = (
                tile / spec.cell_w_mm
                if spec.drop_axis == "column"
                else tile / spec.cell_h_mm
            )
            if not _approx_int(counts * spec.drop_fraction):
                errs.append(
                    f"layer {layer.id!r}: lattice drop does not close on the torus "
                    f"(needs (tile/cell)*drop_fraction integer for drop_axis "
                    f"{spec.drop_axis!r})"
                )
    return errs


def _scatter_errors(layer, placement, tile: float) -> list[str]:
    spec = placement.scatter
    if spec is None:
        return [f"layer {layer.id!r}: scatter placement requires a `scatter` spec"]
    errs: list[str] = []
    if spec.mode == "poisson":
        if spec.min_dist_mm is None:
            errs.append(f"layer {layer.id!r}: scatter poisson requires min_dist_mm")
        elif spec.min_dist_mm > tile / 2:
            errs.append(
                f"layer {layer.id!r}: scatter min_dist_mm {spec.min_dist_mm} exceeds "
                f"tile_mm/2 {tile / 2}"
            )
    else:  # sateen
        if spec.sateen_n is None:
            errs.append(f"layer {layer.id!r}: scatter sateen requires sateen_n")
        else:
            step = spec.sateen_step if spec.sateen_step is not None else 1
            if math.gcd(step, spec.sateen_n) != 1:
                errs.append(
                    f"layer {layer.id!r}: scatter sateen_step {step} must be coprime "
                    f"with sateen_n {spec.sateen_n} (else rows/columns align)"
                )
    return errs


def _point_set_errors(layer, placement, tile: float) -> list[str]:
    spec = placement.point_set
    if spec is None:
        return [f"layer {layer.id!r}: point_set placement requires a `point_set` spec"]
    errs: list[str] = []
    for idx, (x, y) in enumerate(spec.points):
        if not (0 <= x < tile and 0 <= y < tile):
            errs.append(
                f"layer {layer.id!r}: point_set point[{idx}] ({x}, {y}) is outside "
                f"[0, tile_mm={tile})"
            )
    return errs


def _lane_closure(placement, layers_by_id, tile: float) -> tuple[float, int, int] | None:
    """Closure length and snapped slope ``(L, p, q)`` of a path_following lane.

    Returns ``None`` when the lane can't be resolved (host_layer missing or not a
    stripe) -- those cases are reported by other checks. Mirrors how
    ``place_path_following`` resolves the centerline: a host-based lane inherits the host
    stripe's snapped slope (host wins when ``host_layer`` is set), while a standalone
    path snaps its own angle. Host lanes are always straight, so this covers both.
    """
    if placement.host_layer is not None:
        host = layers_by_id.get(placement.host_layer)
        if host is None or host.type != "stripe":
            return None
        snapped = snap_angle(host.params.angle, tile, host.params.period_mm)
    elif placement.path is not None:
        angle = placement.path.angle if placement.path.angle is not None else 0.0
        snapped = snap_angle(angle, tile, tile)
    else:
        return None
    return tile * math.hypot(snapped.p, snapped.q), snapped.p, snapped.q


def validate_intent(raw, *, repair: bool = True) -> ValidationResult:
    # 1. structural
    if isinstance(raw, Intent):
        intent = raw
    else:
        try:
            intent = Intent.model_validate(raw)
        except ValidationError as exc:
            raise IntentInvalid([_fmt_err(e) for e in exc.errors()]) from None

    errors: list[str] = []
    warnings: list[str] = []

    # 2. palette / colorway domain invariants (default colorway, slot coverage)
    try:
        palette = build_palette(intent)
    except ValueError as exc:
        raise IntentInvalid([str(exc)]) from None

    # 3. dpi enum (clamp on repair, else error)
    if intent.canvas.dpi not in ALLOWED_DPI:
        if repair:
            nearest = min(ALLOWED_DPI, key=lambda d: abs(d - intent.canvas.dpi))
            warnings.append(
                f"canvas.dpi {intent.canvas.dpi} not in {ALLOWED_DPI}; clamped to {nearest}"
            )
            intent = intent.model_copy(
                update={"canvas": intent.canvas.model_copy(update={"dpi": nearest})}
            )
        else:
            errors.append(f"canvas.dpi {intent.canvas.dpi} not in {ALLOWED_DPI}")

    # 4. per-colorway resolved color count vs production.max_colors (screen printing
    #    is color-limited; digital is not — ARCHITECTURE.md 색·colorway 모델).
    if intent.production.method == "screen":
        for cw in palette.colorways:
            n = len(palette.distinct_colors(cw.id))
            if n > intent.production.max_colors:
                errors.append(
                    f"colorway {cw.id!r} uses {n} colors > max_colors "
                    f"{intent.production.max_colors}"
                )

    # 5. gamut warnings (non-blocking)
    for cw in palette.colorways:
        for color in sorted(palette.distinct_colors(cw.id)):
            if color.startswith("#") and out_of_gamut(color):
                warnings.append(
                    f"color {color} in colorway {cw.id!r} likely outside CMYK gamut"
                )

    # 6. layer + placement checks
    all_layer_ids = [layer.id for layer in intent.layers]
    layer_ids = set(all_layer_ids)
    layers_by_id = {layer.id: layer for layer in intent.layers}
    if len(all_layer_ids) != len(layer_ids):
        dupes = sorted({i for i in all_layer_ids if all_layer_ids.count(i) > 1})
        errors.append(f"duplicate layer id: {dupes}")
    tile = intent.canvas.tile_mm
    for layer in intent.layers:
        for slot_id in _layer_slot_refs(layer):
            if slot_id not in palette.slot_ids():
                errors.append(
                    f"layer {layer.id!r} references unknown color slot {slot_id!r}"
                )

        if layer.type == "motif":
            # Cross-check the colors<->color_slots contract against the registered
            # motif. An unregistered motif_id is left to compose (unchanged behavior);
            # only a *found* motif is enforced here so a stale catalog can't 422
            # spuriously. Unbound slots are rejected (no `currentColor` leak).
            try:
                motif = get_motif(layer.params.motif_id)
            except ValueError:
                motif = None
            if motif is not None:
                slots = set(motif.color_slots)
                if layer.params.colors is not None:
                    keys = set(layer.params.colors)
                    if keys != slots:
                        errors.append(
                            f"layer {layer.id!r}: colors bind {sorted(keys)} but motif "
                            f"{motif.id!r} has color_slots {sorted(slots)} (every slot "
                            f"must be bound exactly once; no unbound slots)"
                        )
                elif layer.params.color is not None and slots != {"s0"}:
                    errors.append(
                        f"layer {layer.id!r}: motif {motif.id!r} is multi-color "
                        f"(color_slots {sorted(slots)}); use a `colors` mapping"
                    )

        if layer.type == "stripe":
            snapped = snap_angle(layer.params.angle, tile, layer.params.period_mm)
            if not stripe_tiles(tile, layer.params.period_mm, snapped.p, snapped.q):
                errors.append(
                    f"layer {layer.id!r}: stripe (angle {layer.params.angle}, "
                    f"period_mm {layer.params.period_mm}) is not tile-commensurate; a "
                    f"stripe tiles only when tile_mm = k*period_mm*hypot(p, q) "
                    f"(snapped slope {snapped.p}/{snapped.q})"
                )

        placement = getattr(layer, "placement", None)
        if placement is not None:
            if placement.type == "path_following":
                if placement.spacing_mm is None:
                    errors.append(
                        f"layer {layer.id!r}: path_following placement requires spacing_mm"
                    )
                elif placement.spacing_mm <= 0:
                    errors.append(
                        f"layer {layer.id!r}: spacing_mm must be positive, got "
                        f"{placement.spacing_mm}"
                    )
                has_host_lane = (
                    placement.host_layer is not None and placement.lane is not None
                )
                has_host_field = (
                    placement.host_layer is not None or placement.lane is not None
                )
                has_path = placement.path is not None
                if has_path and has_host_field:
                    errors.append(
                        f"layer {layer.id!r}: path_following must specify only one "
                        "mode: host_layer+lane or standalone path"
                    )
                elif not (has_host_lane or has_path):
                    errors.append(
                        f"layer {layer.id!r}: path_following requires either "
                        f"host_layer+lane or a standalone path"
                    )
                # The along-lane step must divide the lane closure length
                # L = tile*hypot(p, q), NOT the tile (L == tile only for an axis-aligned
                # lane). A diagonal closure is irrational for most slopes, so instead of
                # rejecting we snap the step to an exact divisor (see place_path_following)
                # and report the deviation -- mirroring angle commensurate snapping.
                if placement.spacing_mm is not None and placement.spacing_mm > 0:
                    lane = _lane_closure(placement, layers_by_id, tile)
                    if lane is not None:
                        closure, lp, lq = lane
                        if not divides(closure, placement.spacing_mm):
                            n, eff = snap_spacing(closure, placement.spacing_mm)
                            warnings.append(
                                f"layer {layer.id!r}: spacing_mm "
                                f"{placement.spacing_mm} snapped to {eff:.4f}mm for "
                                f"uniform placement (lane closure {closure:.4f} = "
                                f"tile*hypot({lp}, {lq}); {n} instances)"
                            )
            elif placement.type == "lattice":
                errors.extend(_lattice_errors(layer, placement, tile))
            elif placement.type == "scatter":
                errors.extend(_scatter_errors(layer, placement, tile))
            elif placement.type == "point_set":
                errors.extend(_point_set_errors(layer, placement, tile))
            if placement.host_layer is not None:
                if placement.host_layer == layer.id:
                    errors.append(
                        f"layer {layer.id!r}: host_layer cannot reference itself"
                    )
                elif placement.host_layer not in layer_ids:
                    errors.append(
                        f"layer {layer.id!r}: host_layer {placement.host_layer!r} "
                        f"does not exist"
                    )
            if (
                placement.path is not None
                and placement.path.kind == "wave"
                and placement.path.wavelength is not None
            ):
                # Seam continuity needs the sinusoid to return to phase 0 at the lane
                # closure length L = tile*hypot(p, q), NOT merely at the tile. For an
                # axis-aligned lane L == tile; for a diagonal it is larger (and, for a
                # non-Pythagorean slope, irrational -> only sub-multiples close).
                angle = placement.path.angle if placement.path.angle is not None else 0.0
                snapped = snap_angle(angle, tile, tile)
                closure = tile * math.hypot(snapped.p, snapped.q)
                if not divides(closure, placement.path.wavelength):
                    errors.append(
                        f"layer {layer.id!r}: wave wavelength "
                        f"{placement.path.wavelength} does not divide the lane closure "
                        f"length {closure} (tile*hypot({snapped.p}, {snapped.q}))"
                    )

    sym = intent.symmetry
    if sym is not None and sym.shift_mm is not None and not divides(tile, sym.shift_mm):
        errors.append(
            f"symmetry shift_mm {sym.shift_mm} does not divide tile_mm {tile}"
        )

    if errors:
        raise IntentInvalid(errors)
    return ValidationResult(intent=intent, palette=palette, warnings=warnings)
