"""Multi-candidate orchestration: deterministic diversification, ranking, de-dup.

The single-candidate pipeline lives in :mod:`app.engine.generate`. This module
sits one level up and turns a single base intent into a ranked, de-duplicated set
of candidates by branching along three deterministic axes — layout (placement +
symmetry), colorway, and seed — so the same request with the same seed always
yields the same candidate set (``request_id`` aside, which is request metadata).

Kept in the engine layer (not the API route) so the determinism contract stays
inside the engine boundary; the route is a thin adapter over ``generate_candidates``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass

from app.engine.composition import compose
from app.engine.determinism import ReproMeta, layout_id_for
from app.engine.generate import Candidate
from app.engine.intent import Intent, SymmetrySpec
from app.engine.palette import Palette
from app.engine.seamless import assert_seamless_invariants
from app.validate.intent import IntentInvalid, validate_intent

DEFAULT_CANDIDATE_COUNT = 4
MAX_CANDIDATE_COUNT = 8

# Intent-direct path has no reference image, so reproduction is exact/vector.
SOURCE_FIDELITY_VECTOR = "vector"

# Deterministic, fixed-order diversity axes.
_SYMMETRY_KINDS: tuple[str, ...] = ("mirror_2x2", "mirror_h", "mirror_v", "glide_h")
_DROP_FRACTIONS: tuple[float | None, ...] = (None, 0.5, 1.0 / 3.0, 0.25)
# Coarse placement-type rank used only as a deterministic tiebreak (NOT a true spread
# metric): path_following lanes tend to align more than all-over scatter, so they sort
# later. Lower == preferred.
_PLACEMENT_RANK = {"path_following": 2, "lattice": 1, "point_set": 1, "scatter": 0}


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
                repro = ReproMeta.build(
                    intent_version=eff.intent_version,
                    seed=s,
                    colorway_id=cw,
                    layout_id=lid,
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


def _layout_variants(base: Intent) -> Iterator[Intent]:
    """Deterministic layout variants of a base intent (identity first)."""
    yield base
    base_kind = base.symmetry.kind if base.symmetry is not None else None
    for kind in _SYMMETRY_KINDS:
        if kind == base_kind:
            continue
        yield base.model_copy(update={"symmetry": SymmetrySpec(kind=kind)})
    for idx, layer in enumerate(base.layers):
        if not _is_lattice_layer(layer):
            continue
        current = layer.placement.lattice.drop_fraction
        for frac in _DROP_FRACTIONS:
            if frac == current:
                continue
            yield _with_lattice_drop(base, idx, frac)


def _with_lattice_drop(base: Intent, layer_idx: int, frac: float | None) -> Intent:
    clone = base.model_copy(deep=True)
    clone.layers[layer_idx].placement.lattice.drop_fraction = frac
    return clone


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


def _candidate_id(layout_id: str, colorway_id: str, seed: int) -> str:
    raw = f"{layout_id}:{colorway_id}:{seed}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
