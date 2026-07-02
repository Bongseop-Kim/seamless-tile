"""Multi-candidate orchestration: deterministic diversification, ranking, de-dup.

The single-candidate pipeline lives in :mod:`app.engine.generate`. This module
sits one level up and turns a single base intent into a ranked, de-duplicated set
of candidates by branching along three deterministic axes — layout (placement),
colorway, and seed — so the same request with the same seed always
yields the same candidate set (``request_id`` aside, which is request metadata).

Kept in the engine layer (not the API route) so the determinism contract stays
inside the engine boundary; the route is a thin adapter over ``generate_candidates``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, replace

from app.core.config import REGISTRY_VERSION
from app.engine.composition import compose
from app.engine.determinism import ReproMeta, layout_id_for
from app.engine.generate import Candidate
from app.engine.intent import Band, Intent
from app.engine.palette import Palette
from app.engine.seamless import assert_seamless_invariants
from app.validate.intent import IntentInvalid, validate_intent

DEFAULT_CANDIDATE_COUNT = 4
MAX_CANDIDATE_COUNT = 8

# Intent-direct path has no reference image, so reproduction is exact/vector.
SOURCE_FIDELITY_VECTOR = "vector"

# Deterministic, fixed-order diversity axes.
_DROP_FRACTIONS: tuple[float | None, ...] = (None, 0.5, 1.0 / 3.0, 0.25)
# Coarse placement-type rank used only as a deterministic tiebreak (NOT a true spread
# metric): path_following lanes tend to align more than all-over scatter, so they sort
# later. Lower == preferred.
_PLACEMENT_RANK = {"path_following": 2, "lattice": 1, "point_set": 1, "scatter": 0}

# Uneven/guard-stripe rhythm presets (authentic repp is unbalanced, not equal-width).
# Each is (band weights, between-band gap weight): widths/gaps are weight*u with
# u = period/total, so bands+gaps partition exactly one period (seamless), and colors only
# cycle the layer's existing band colors. Literal order == emission order.
_STRIPE_RHYTHMS_SINGLE: tuple[tuple[tuple[float, ...], float], ...] = (
    ((5.0, 2.0, 2.0), 0.5),  # guard_5_2_2: wide stripe + thin guard pair (3 bands)
    ((3.0, 2.0, 1.0), 0.6),  # asym_3_2_1: graduated uneven cluster (3 bands)
)
_STRIPE_RHYTHMS_MULTI: tuple[tuple[tuple[float, ...], float], ...] = (
    ((5.0, 11.0), 0.4),  # ratio_5_11: uneven two-band, cycle existing colors
    ((6.0, 1.0, 3.0), 0.4),  # asym_6_1_3: uneven three-band, cycle existing colors
)

@dataclass(frozen=True)
class RankedCandidate:
    """One ranked candidate: the composed SVG plus the variant that produced it."""

    id: str
    candidate: Candidate  # svg, repro (with layout_id), layout_id
    intent: Intent  # the validated variant intent actually composed (seed applied)
    colorway_id: str
    seed: int
    source_fidelity: str
    rank_key: tuple
    design_index: int = 0


@dataclass(frozen=True)
class CandidateSet:
    candidates: list[RankedCandidate]
    warnings: list[str]
    available_strategy_count: int


def generate_candidates(
    base_raw,
    *,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    seed: int | None = None,
    colorway: str | None = None,
    source_fidelity: str = SOURCE_FIDELITY_VECTOR,
    registry_version: str = REGISTRY_VERSION,
) -> CandidateSet:
    """Diversify a base intent into a ranked, de-duplicated candidate set.

    Raises ``IntentInvalid`` / ``AssertionError`` / ``ValueError`` for a base intent
    that fails validation or by-construction seamless invariants (the route maps
    these to 422). Variant-level failures along the diversity axes are dropped and
    surfaced as warnings (partial success), not raised.
    """
    # Defensive clamp; the HTTP boundary (ExportRequest/GenerateRequest schema) is the
    # authoritative validator and already rejects out-of-range counts with a 400.
    count = max(1, min(int(candidate_count), MAX_CANDIDATE_COUNT))

    base = validate_intent(base_raw)
    base_intent = base.intent
    assert_seamless_invariants(base_intent)
    warnings = list(base.warnings)

    available_cws = [cw.id for cw in base_intent.colorways]
    if colorway is not None:
        if colorway not in available_cws:
            raise ValueError(
                f"unknown colorway {colorway!r}; available: {available_cws}"
            )
        colorways = [colorway]
    else:
        colorways = available_cws

    base_seed = base_intent.seed if seed is None else int(seed)

    # 1. Layout variants: validate + seamless-check each, de-dup by layout_id.
    variants: list[tuple[str, Intent, Palette]] = []
    seen_layouts: set[str] = set()
    for variant in _layout_variants(base_intent):
        try:
            res = validate_intent(variant)
            assert_seamless_invariants(res.intent)
        except (IntentInvalid, AssertionError, ValueError):
            continue  # incompatible axis value for this base intent
        lid = layout_id_for(res.intent)
        if lid in seen_layouts:
            continue
        seen_layouts.add(lid)
        variants.append((lid, res.intent, res.palette))

    available_strategy_count = len(variants)

    # Seed axis only manufactures distinct content when a scatter layer consumes it, and
    # only adds value when the layout x colorway pool alone cannot fill candidate_count.
    seeds = [base_seed]
    if _has_scatter(base_intent) and len(variants) * len(colorways) < count:
        seeds += [base_seed + i for i in range(1, count + 1)]

    # 2. Generate the pool.
    pool: list[RankedCandidate] = []
    render_failures = 0
    for lid, intent_v, palette_v in variants:
        clustering = _clustering_score(intent_v)
        for cw in colorways:
            color_count = len(palette_v.distinct_colors(cw))
            for s in seeds:
                eff = intent_v.model_copy(update={"seed": s})
                try:
                    svg = compose(eff, palette_v, cw)
                except (AssertionError, ValueError, IntentInvalid):
                    # Recoverable, by-construction combo failure; drop it. Other
                    # exception types are real bugs and intentionally propagate.
                    render_failures += 1
                    continue
                repro = ReproMeta(
                    intent_version=eff.intent_version,
                    seed=s,
                    colorway_id=cw,
                    layout_id=lid,
                    registry_version=registry_version,
                )
                pool.append(
                    RankedCandidate(
                        id=_candidate_id(lid, cw, s),
                        candidate=Candidate(
                            svg=svg, repro=repro, warnings=[], layout_id=lid
                        ),
                        intent=eff,
                        colorway_id=cw,
                        seed=s,
                        source_fidelity=source_fidelity,
                        rank_key=(color_count, clustering, lid, cw, s),
                    )
                )
    if render_failures:
        warnings.append(
            f"{render_failures} candidate variant(s) failed to render and were dropped"
        )

    # 3. De-dup identical SVGs, keeping the best-ranked representative.
    best_by_svg: dict[str, RankedCandidate] = {}
    for rc in pool:
        prev = best_by_svg.get(rc.candidate.svg)
        if prev is None or rc.rank_key < prev.rank_key:
            best_by_svg[rc.candidate.svg] = rc
    deduped = sorted(best_by_svg.values(), key=lambda rc: rc.rank_key)

    # 4. Select: candidate_count first (priority 1), layout diversity second.
    selected: list[RankedCandidate] = []
    seen: set[str] = set()
    for rc in deduped:  # pass 1: best per distinct layout
        if len(selected) >= count:
            break
        if rc.candidate.layout_id not in seen:
            seen.add(rc.candidate.layout_id)
            selected.append(rc)
    if len(selected) < count:  # pass 2: fill remaining slots
        chosen = {rc.id for rc in selected}
        for rc in deduped:
            if len(selected) >= count:
                break
            if rc.id not in chosen:
                selected.append(rc)
    selected.sort(key=lambda rc: rc.rank_key)

    # 5. Diversity / count warnings.
    distinct_selected = len({rc.candidate.layout_id for rc in selected})
    if count >= 2:
        required = min(2, available_strategy_count)
        if distinct_selected < required:
            warnings.append(
                f"diversity shortfall: {distinct_selected} distinct layout(s) "
                f"< required {required}"
            )
    if len(selected) < count:
        warnings.append(
            f"partial: {len(selected)} candidate(s) available after de-dup "
            f"(requested {count})"
        )

    return CandidateSet(
        candidates=selected,
        warnings=warnings,
        available_strategy_count=available_strategy_count,
    )


def generate_candidate_set(
    base_raws,
    *,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    seed: int | None = None,
    colorway: str | None = None,
    source_fidelity: str = SOURCE_FIDELITY_VECTOR,
    registry_version: str = REGISTRY_VERSION,
) -> CandidateSet:
    """Diversify and merge MULTIPLE base intents ("designs") into one ranked set.

    Each design is diversified by the single-base :func:`generate_candidates`; the
    results are globally de-duplicated by SVG, then selected round-robin across designs
    (cross-design diversity first) up to ``candidate_count``. A one-element list
    reproduces :func:`generate_candidates` exactly (svg + ids). Invalid designs are
    dropped with a warning; if every design is invalid the last error is raised (the
    route maps it to 422, same as the single-base path).
    """
    count = max(1, min(int(candidate_count), MAX_CANDIDATE_COUNT))

    designs = list(base_raws)

    warnings: list[str] = []
    per_design: list[list[RankedCandidate]] = []
    available = 0
    last_exc: Exception | None = None
    for i, base_raw in enumerate(designs):
        try:
            cs = generate_candidates(
                base_raw,
                candidate_count=count,
                seed=seed,
                colorway=colorway,
                source_fidelity=source_fidelity,
                registry_version=registry_version,
            )
        except (IntentInvalid, AssertionError, ValueError) as exc:
            last_exc = exc
            warnings.append(f"design {i} dropped: {exc}")
            continue
        # Re-tag each candidate with its design index and a namespaced id (design 0 keeps
        # the original hash, so the single-base case is byte/id identical).
        tagged = [
            replace(
                rc,
                design_index=i,
                id=_candidate_id(rc.candidate.layout_id, rc.colorway_id, rc.seed, i),
            )
            for rc in cs.candidates
        ]
        per_design.append(tagged)
        # Per-design shortfall/partial artifacts are re-derived at the multi level below.
        warnings.extend(
            w for w in cs.warnings if not w.startswith(("diversity shortfall:", "partial:"))
        )
        available += cs.available_strategy_count

    if not per_design:
        if last_exc is not None:
            raise last_exc
        raise ValueError("no base intents to generate candidates from")

    # Global SVG de-dup across designs: keep the best-ranked representative, tie-broken on
    # the lower design index (deterministic; comparison is on pre-built tuples).
    best_by_svg: dict[str, RankedCandidate] = {}
    for rc in (rc for design in per_design for rc in design):
        prev = best_by_svg.get(rc.candidate.svg)
        if prev is None or (rc.rank_key, rc.design_index) < (
            prev.rank_key,
            prev.design_index,
        ):
            best_by_svg[rc.candidate.svg] = rc

    # Regroup survivors per surviving design (each sorted by rank_key); selection consumes
    # lists in ascending-design-index order, so set/dict iteration order never reaches the
    # output. Keyed by design_index (which may be sparse when designs were dropped).
    groups_by_design: dict[int, list[RankedCandidate]] = {}
    for rc in sorted(best_by_svg.values(), key=lambda rc: (rc.design_index, rc.rank_key)):
        groups_by_design.setdefault(rc.design_index, []).append(rc)
    groups: list[list[RankedCandidate]] = [
        groups_by_design[d] for d in sorted(groups_by_design)
    ]

    # Round-robin: one best-remaining candidate per design per pass.
    selected: list[RankedCandidate] = []
    cursors = [0] * len(groups)
    progressed = True
    while len(selected) < count and progressed:
        progressed = False
        for gi, group in enumerate(groups):
            if len(selected) >= count:
                break
            if cursors[gi] < len(group):
                selected.append(group[cursors[gi]])
                cursors[gi] += 1
                progressed = True
    selected.sort(key=lambda rc: rc.rank_key)

    distinct_designs = len({rc.design_index for rc in selected})
    if count >= 2:
        required = min(2, len(per_design), available)
        if distinct_designs < required:
            warnings.append(
                f"diversity shortfall: {distinct_designs} distinct design(s) "
                f"< required {required}"
            )
    if len(selected) < count:
        warnings.append(
            f"partial: {len(selected)} candidate(s) available after de-dup "
            f"(requested {count})"
        )

    warnings = list(dict.fromkeys(warnings))
    return CandidateSet(
        candidates=selected,
        warnings=warnings,
        available_strategy_count=available,
    )


def _layout_variants(base: Intent) -> Iterator[Intent]:
    """Deterministic layout variants of a base intent (identity first)."""
    yield base
    for idx, layer in enumerate(base.layers):
        if layer.type == "stripe":
            yield from _stripe_variants(base, idx)
            continue
        if not _is_lattice_layer(layer):
            placement = getattr(layer, "placement", None)
            if (
                layer.type == "motif"
                and placement is not None
                and placement.type == "path_following"
            ):
                for spacing in (placement.spacing_mm * 0.75, placement.spacing_mm * 1.5):
                    updated_layers = list(base.layers)
                    updated_layers[idx] = layer.model_copy(
                        update={
                            "placement": placement.model_copy(
                                update={"spacing_mm": _q(spacing)}
                            )
                        }
                    )
                    yield base.model_copy(update={"layers": updated_layers})
                yield from _motif_size_variants(base, idx)
            continue
        current = layer.placement.lattice.drop_fraction
        for frac in _DROP_FRACTIONS:
            if frac == current:
                continue
            yield _with_lattice_drop(base, idx, frac)
        yield from _lattice_cell_variants(base, idx)
        yield from _motif_size_variants(base, idx)


def _q(value: float) -> float:
    return round(float(value), 6)


def _stripe_variants(base: Intent, layer_idx: int) -> Iterator[Intent]:
    # Stripe count/period is fixed (normalized to 45 deg, k repeats on the prompt path);
    # diversify only the band STRUCTURE within one period, never the period (count).
    layer = base.layers[layer_idx]
    params = layer.params
    if len(params.bands) == 1:
        current = params.bands[0].width_mm / params.period_mm
        for ratio in (0.35, 0.65):
            if abs(ratio - current) > 1e-6:
                yield _with_stripe_band_ratio(base, layer_idx, ratio)
        rhythms = _STRIPE_RHYTHMS_SINGLE
    else:
        rhythms = _STRIPE_RHYTHMS_MULTI
    for weights, gap in rhythms:
        yield _with_stripe_rhythm(base, layer_idx, weights, gap)



def _with_stripe_band_ratio(base: Intent, layer_idx: int, ratio: float) -> Intent:
    layer = base.layers[layer_idx]
    params = layer.params
    band = params.bands[0]
    updated_band = band.model_copy(update={"width_mm": _q(params.period_mm * ratio)})
    updated_layers = list(base.layers)
    updated_layers[layer_idx] = layer.model_copy(
        update={
            "params": params.model_copy(update={"bands": [updated_band]})
        }
    )
    return base.model_copy(update={"layers": updated_layers})


def _with_stripe_rhythm(
    base: Intent, layer_idx: int, weights: tuple[float, ...], gap_weight: float
) -> Intent:
    """Replace a stripe's bands with a rhythm partitioning exactly one period_mm.

    ``period_mm`` and ``angle`` are untouched, so the stripe stays tile-commensurate
    (seamless preserved). Band widths/gaps are ``weight * u`` with
    ``u = period / (sum(weights) + gap_weight * (n - 1))`` so the bands (plus the gaps
    between them) sum to exactly one period. Colors only cycle the layer's existing band
    colors in order — no new colors, no palette/colorway change.
    """
    layer = base.layers[layer_idx]
    params = layer.params
    period = params.period_mm
    base_colors = [b.color for b in params.bands]  # len >= 1 (schema)
    n = len(weights)
    total = sum(weights) + gap_weight * (n - 1)
    u = period / total
    bands: list[Band] = []
    cursor = 0.0
    for i, w in enumerate(weights):
        width = w * u
        bands.append(
            Band(
                offset_mm=_q(cursor),
                width_mm=_q(width),
                color=base_colors[i % len(base_colors)],
            )
        )
        cursor += width
        if i < n - 1:
            cursor += gap_weight * u
    updated_layers = list(base.layers)
    updated_layers[layer_idx] = layer.model_copy(
        update={"params": params.model_copy(update={"bands": bands})}
    )
    return base.model_copy(update={"layers": updated_layers})


def _lattice_cell_variants(base: Intent, layer_idx: int) -> Iterator[Intent]:
    layer = base.layers[layer_idx]
    spec = layer.placement.lattice
    tile = base.canvas.tile_mm
    nx = max(1, round(tile / spec.cell_w_mm))
    ny = max(1, round(tile / spec.cell_h_mm))
    for nxx, nyy in ((nx + 1, ny + 1), (max(1, nx - 1), max(1, ny - 1))):
        if nxx == nx and nyy == ny:
            continue
        yield _with_lattice_cells(base, layer_idx, tile / nxx, tile / nyy)


def _with_lattice_cells(
    base: Intent, layer_idx: int, cell_w: float, cell_h: float
) -> Intent:
    layer = base.layers[layer_idx]
    placement = layer.placement
    spec = placement.lattice
    updated_layers = list(base.layers)
    updated_layers[layer_idx] = layer.model_copy(
        update={
            "placement": placement.model_copy(
                update={
                    "lattice": spec.model_copy(
                        update={"cell_w_mm": _q(cell_w), "cell_h_mm": _q(cell_h)}
                    )
                }
            )
        }
    )
    return base.model_copy(update={"layers": updated_layers})


def _motif_size_variants(base: Intent, layer_idx: int) -> Iterator[Intent]:
    layer = base.layers[layer_idx]
    size = layer.params.size_mm
    for factor in (0.75, 1.35):
        new_size = min(base.canvas.tile_mm, size * factor)
        if abs(new_size - size) > 1e-6:
            yield _with_motif_size(base, layer_idx, new_size)


def _with_motif_size(base: Intent, layer_idx: int, size: float) -> Intent:
    layer = base.layers[layer_idx]
    updated_layers = list(base.layers)
    updated_layers[layer_idx] = layer.model_copy(
        update={"params": layer.params.model_copy(update={"size_mm": _q(size)})}
    )
    return base.model_copy(update={"layers": updated_layers})


def _with_lattice_drop(base: Intent, layer_idx: int, frac: float | None) -> Intent:
    layer = base.layers[layer_idx]
    placement = layer.placement
    lattice = placement.lattice
    updated_layers = list(base.layers)
    updated_layers[layer_idx] = layer.model_copy(
        update={
            "placement": placement.model_copy(
                update={"lattice": lattice.model_copy(update={"drop_fraction": frac})}
            )
        }
    )
    return base.model_copy(update={"layers": updated_layers})


def _is_lattice_layer(layer) -> bool:
    placement = getattr(layer, "placement", None)
    return (
        layer.type == "motif"
        and placement is not None
        and placement.type == "lattice"
        and placement.lattice is not None
    )


def _has_scatter(intent: Intent) -> bool:
    return any(
        getattr(layer, "placement", None) is not None
        and layer.placement.type == "scatter"
        for layer in intent.layers
    )


def _clustering_score(intent: Intent) -> int:
    score = 0
    for layer in intent.layers:
        placement = getattr(layer, "placement", None)
        if placement is not None:
            score += _PLACEMENT_RANK.get(placement.type, 0)
    return score


def _candidate_id(
    layout_id: str, colorway_id: str, seed: int, design_index: int = 0
) -> str:
    # design_index 0 reproduces the original hash (single-base back-compat); >0 is
    # namespaced so two designs sharing a (layout_id, colorway, seed) cannot collide.
    key = layout_id if design_index == 0 else f"{design_index}:{layout_id}"
    raw = f"{key}:{colorway_id}:{seed}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
