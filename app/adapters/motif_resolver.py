"""Deterministic motif-resolution glue (spec §6, P0 + S11 soft similarity).

The flow: ``prompt`` → LLM (``intent`` + ``motif_specs``) → **this module** → a concrete
``motif_id`` injected into each motif layer of the intent → engine ``compose``. The
selection is a *pure, deterministic* step; the non-deterministic pieces (embedding
search, miss-path SVG generation) are frozen by adapter caches, so the determinism
contract holds: the engine only ever sees an intent with concrete motif ids.

Retrieval (spec §6.1, D18): **exact descriptor match** → **scope hard filter** →
**embedding soft similarity (τ gate)** → **generate-on-miss**. Every hit routes through
the variant_group's reusable sampling pool (§7.1), **τ-scoped to the query embedding** so
a sibling that only shares (subject, scope) — a different part living in ``description``,
e.g. a "giraffe leg" in a "giraffe face" group — can't be sampled in place of the match;
when the pool is empty it falls back to the matched motif. The embedding stage is
fail-soft: if no embedding client is
configured, or the call fails, or no candidate has a comparable embedding, it degrades
to the S10 lowest-id hard-filter reuse.
"""

from __future__ import annotations

import copy
import math

from app.adapters.base import AdapterClientError
from app.adapters.embedding import EmbeddingError, embed_query
from app.adapters.recraft import generate_via_recraft, vectorize_via_recraft
from app.core.config import get_settings
from app.core.observability import log_metrics
from app.engine import determinism
from app.motifs import facets, glyph_builder
from app.motifs.store import MotifStoreError, get_default_store

# Facets that define an "exact descriptor" (spec §6.1, D18). `description` carries the
# part/anatomy name (D10: "wing"/"leg"/... live here, not in `subject`), so it MUST be
# compared — otherwise a stored "giraffe leg" descriptor exactly matches a "giraffe face"
# query (both giraffe/partial with empty view/expression/style) and the wrong part is
# picked as the reuse anchor before the embedding stage ever runs.
_EXACT_FACETS = ("subject", "scope", "view", "expression", "style", "description")


def _tau() -> float:
    """Cosine similarity threshold for "reuse vs generate" (spec §6.1/D13)."""
    return get_settings().motif_similarity_tau


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 when either vector has zero norm. Mirrors the store's
    pgvector ``1 - (a <=> b)`` so the in-Python variant-pool filter agrees with the
    DB-side ranking that selected the match."""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _similar_enough(embedding, query_vec: list[float], tau: float) -> bool:
    """Whether a variant-pool sibling may stand in for the match. A missing or
    incompatibly-sized embedding is unjudgeable and kept (``True``); otherwise it must be
    within ``tau`` of the query. Only a comparable-but-below-τ sibling is dropped, so the
    filter can never empty a pool of genuinely-similar (or pre-embedding) variants."""
    if not embedding or len(embedding) != len(query_vec):
        return True
    return _cosine(embedding, query_vec) >= tau


def _descriptor_text(spec: dict) -> str:
    """Embedding source text for a spec (D12: a normalized English descriptor).

    Prefers an explicit ``description``; otherwise synthesizes one from the facets with a
    FIXED algorithm so two implementations produce the same string: empty facets are
    dropped, tokens are single-spaced, and there are no dangling commas. ``scope`` is a
    granularity guardrail (whole/partial), not a meaning token, so it is deliberately
    left out of the embedding text — it already separates candidates via the hard filter.
    """
    description = (spec.get("description") or "").strip()
    if description:
        return description
    subject = (spec.get("subject") or "").strip()
    expression = (spec.get("expression") or "").strip()
    style = (spec.get("style") or "").strip()
    view = (spec.get("view") or "").strip()
    head = " ".join(t for t in (expression, subject) if t)
    view_clause = f"{view} view" if view else ""
    return ", ".join(seg for seg in (head, view_clause, style) if seg)


def _exact_match(spec: dict, candidates: list) -> str | None:
    """The candidate whose full normalized descriptor equals the spec, else ``None``.

    Candidates arrive ``ORDER BY id`` so the choice is stable across calls.
    """
    want = tuple(facets.normalize_facet(spec.get(k)) for k in _EXACT_FACETS)
    for rec in candidates:
        have = tuple(facets.normalize_facet(getattr(rec, k)) for k in _EXACT_FACETS)
        if have == want:
            return rec.id
    return None


def _log_path(
    path: str, spec: dict, *, similarity: float | None = None, selected_id: str = "-"
) -> None:
    """One structured line per resolution for τ calibration (#7, spec §12).

    ``path`` is exact|vector|fallback|generate. ``similarity`` is the cosine of the best
    candidate when known (vector hit, or a below-τ generate), else ``-``.
    """
    log_metrics(
        "motif_resolve",
        path=path,
        scope=facets.normalize_facet(spec.get("scope")) or "-",
        subject=(spec.get("subject") or "-"),
        similarity=("-" if similarity is None else round(similarity, 4)),
        selected_id=selected_id,
    )


def _select_variant(
    store, variant_group, seed: int, fallback_id: str, query_vec=None
) -> str:
    """Seed-sample one variant from the group's reusable pool (§7.1), else ``fallback_id``.

    ``variant_group`` is keyed on (subject, scope), so a pool can hold genuinely
    interchangeable variants (five- vs six-petal "flower"/"whole") AND, when the part
    lives only in ``description``, semantically-different siblings ("giraffe face" vs
    "giraffe leg", both giraffe/partial). When ``query_vec`` is given, the pool is
    therefore restricted to members within τ of the query embedding — the anchor
    ``fallback_id`` and members lacking a comparable embedding are always kept — so a
    dissimilar sibling can't be sampled in place of the match. Without ``query_vec``
    (embedding unavailable) the whole group is the pool, as before. Empty group →
    ``fallback_id``.
    """
    if not variant_group:
        return fallback_id
    try:
        members = store.find_by_variant_group(variant_group)
    except MotifStoreError:
        return fallback_id
    if query_vec is None:
        pool = [rec.id for rec in members]
    else:
        tau = _tau()
        pool = [
            rec.id
            for rec in members
            if rec.id == fallback_id or _similar_enough(rec.embedding, query_vec, tau)
        ]
    if not pool:
        return fallback_id
    return determinism.select_variant(pool, variant_group, seed)


def _resolve_one(
    spec: dict, *, store, recraft_client, embedding_client, seed: int
) -> str:
    # Normalize the controlled facet so the DB filter, the exact-match comparison, and
    # the generated motif's stored facets all agree (NFC + strip + casefold). `scope` is
    # the only hard filter; `subject` (free text) discrimination is the embedding's job.
    scope = facets.normalize_facet(spec.get("scope"))
    query_vec: list[float] | None = None
    best_sim: float | None = None  # carried into the generate log for τ calibration
    if scope and store is not None:
        try:
            candidates = store.find_facets_meta(scope)
        except MotifStoreError:
            # A flaky DB read is treated as a miss (graceful, spec §6.4): regeneration is
            # idempotent via the content-hash id, so correctness is preserved.
            candidates = []
        if candidates:
            # Embed up front: the query vector scopes EVERY hit's variant pool
            # (_select_variant), so a sibling that merely shares (subject, scope) but is
            # semantically different — a "giraffe leg" in a "giraffe face" group — can't
            # be sampled in place of the match. embed_query returns None when embedding is
            # *unconfigured* (no client/key → graceful, coarse pool); a real call FAILURE
            # raises AdapterClientError, which we let propagate → 502 rather than silently
            # reusing an arbitrary motif and hiding the outage.
            query_vec = embed_query(_descriptor_text(spec), client=embedding_client)
            # (0) Exact descriptor match wins (D18); route through the group's pool.
            exact = _exact_match(spec, candidates)
            if exact is not None:
                rec = next(c for c in candidates if c.id == exact)
                selected = _select_variant(
                    store, rec.variant_group, seed, exact, query_vec
                )
                _log_path("exact", spec, selected_id=selected)
                return selected
            # (2) Soft similarity, ranked in Postgres.
            match = None
            if query_vec is not None:
                try:
                    match = store.find_best_by_embedding(scope, query_vec)
                except MotifStoreError:
                    match = None
            if match is None:
                # Embedding unavailable / no comparable candidate → S10 hard-filter reuse
                # (reuse-first, lowest id), routed through the variant pool.
                fallback = min(candidates, key=lambda c: c.id)
                selected = _select_variant(
                    store, fallback.variant_group, seed, fallback.id, query_vec
                )
                _log_path("fallback", spec, selected_id=selected)
                return selected
            best_sim = match.similarity
            if best_sim >= _tau():  # τ or above → reuse (hit)
                selected = _select_variant(
                    store, match.variant_group, seed, match.id, query_vec
                )
                _log_path("vector", spec, similarity=best_sim, selected_id=selected)
                return selected
            # below τ → miss (generate); fall through.
    # Miss (or missing facets / no store) → generate via Recraft, persisting the query
    # embedding so future requests can soft-match. May raise AdapterClientError (→ 502)
    # if the generated SVG is unsanitizable or no Recraft client is configured.
    new_id = generate_via_recraft(spec, client=recraft_client, embedding=query_vec)
    _log_path("generate", spec, similarity=best_sim, selected_id=new_id)
    return new_id


def present_candidates(
    spec: dict,
    *,
    store=None,
    embedding_client=None,
    k: int | None = None,
) -> list[dict]:
    """Free reuse candidates for a motif spec — the interactive gate's read-only
    counterpart to :func:`_resolve_one`'s retrieval ladder, stopping BEFORE
    ``generate_via_recraft`` (spec §8.3, S12). Presenting reuse options is free; the
    expensive Recraft generation is gated on an explicit user confirm elsewhere, so this
    function NEVER calls Recraft.

    Returns up to ``k`` ``{"motif_id", "similarity"}`` dicts, best first: the exact
    descriptor match (similarity 1.0) → the best embedding match (its cosine) → scope-pool
    fill by lowest id (similarity ``None``). Empty when there is no store/scope/pool.
    """
    if k is None:
        k = get_settings().motif_candidate_top_k
    if store is None:
        store = get_default_store()
    scope = facets.normalize_facet(spec.get("scope"))
    if not scope or store is None:
        return []
    try:
        candidates = store.find_facets_meta(scope)
    except MotifStoreError:
        return []
    if not candidates:
        return []

    ranked: list[tuple[str, float | None]] = []
    seen: set[str] = set()
    exact = _exact_match(spec, candidates)
    if exact is not None:
        ranked.append((exact, 1.0))
        seen.add(exact)
    # embed_query is None when embeddings are unconfigured. Presentation is a gate UI
    # helper, so embedding outages are soft failures here: keep exact/id-order candidates.
    try:
        query_vec = embed_query(_descriptor_text(spec), client=embedding_client)
    except EmbeddingError:
        query_vec = None
    if query_vec is not None:
        try:
            match = store.find_best_by_embedding(scope, query_vec)
        except MotifStoreError:
            match = None
        if match is not None and match.id not in seen:
            ranked.append((match.id, round(match.similarity, 4)))
            seen.add(match.id)
    # ponytail: fill the rest from the scope pool in id order (candidates arrive ORDER BY
    # id). This is exact + best-embedding + id-fill, NOT a true embedding top-K query —
    # add a store.find_top_by_embedding(scope, vec, k) if ranking the tail matters.
    for rec in candidates:
        if len(ranked) >= k:
            break
        if rec.id not in seen:
            ranked.append((rec.id, None))
            seen.add(rec.id)
    return [{"motif_id": mid, "similarity": sim} for mid, sim in ranked[:k]]


def _resolve_text_layer(spec: dict, layer: dict, resolved: dict, sink: list[str]) -> str:
    lid = layer.get("id")
    params = layer.setdefault("params", {})
    slots = {
        s.get("id")
        for s in ((resolved.get("palette") or {}).get("slots") or [])
        if isinstance(s, dict) and isinstance(s.get("id"), str)
    }
    default_color = params.get("color")
    if not isinstance(default_color, str) or default_color not in slots:
        colors = params.get("colors") if isinstance(params.get("colors"), dict) else {}
        default_color = next((c for c in colors.values() if c in slots), None)
    if default_color is None:
        default_color = next(iter(sorted(slots)), None)
    if default_color is None:
        raise AdapterClientError("text motif requires at least one palette slot")

    try:
        text_motif = glyph_builder.build_text_motif(
            spec.get("text"),
            spec.get("segments"),
            default_color=default_color,
            valid_color_slots=slots,
        )
    except ValueError as exc:
        raise AdapterClientError(str(exc)) from exc

    for w in text_motif.warnings:
        sink.append(f"text layer {lid!r}: {w}")
    params["motif_id"] = text_motif.motif_id
    if text_motif.colors:
        params.pop("color", None)
        params["colors"] = text_motif.colors
    else:
        params.pop("colors", None)
        params["color"] = text_motif.color
    return text_motif.motif_id


def resolve_motifs(
    intent: dict,
    motif_specs: list[dict],
    *,
    store=None,
    recraft_client=None,
    embedding_client=None,
    seed: int = 0,
    images: list[bytes] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Return a copy of ``intent`` with each motif layer's ``params.motif_id`` resolved.

    Each spec is matched to a layer by ``layer_id``: exact descriptor match → subject/
    part hard-filter → embedding soft similarity (τ) → generate-on-miss, with hits
    seed-sampled from the reusable variant pool. ``seed`` must be the SAME effective seed
    the engine composes with (the route unifies it) so variant selection and composition
    agree. Layers without a matching spec are left untouched.

    Tier-1 gate handling (spec §6.4): when a motif exhausts its sanitize/structure gate
    (the adapter already regenerated once), its layer is dropped instead of failing the
    whole request, and any layer that hosts on a dropped layer is dropped too (cascade,
    so a dangling ``host_layer`` cannot turn partial success into a 422). If at least one
    motif still resolves, the surviving intent is returned and a drop warning per dropped
    layer is appended to ``warnings`` (partial success → 200). If every attempted motif
    fails — or the cascade leaves no layers — an :class:`AdapterClientError` is raised
    (the route maps it to 502).
    """
    if not motif_specs:
        return intent
    if store is None:
        store = get_default_store()

    sink = warnings if warnings is not None else []
    resolved = copy.deepcopy(intent)
    layers_by_id = {
        layer.get("id"): layer
        for layer in resolved.get("layers", [])
        if isinstance(layer, dict)
    }
    attempted: set[str] = set()
    failed: set[str] = set()
    reasons: dict[str, str] = {}
    for spec in motif_specs:
        layer = layers_by_id.get(spec.get("layer_id"))
        if layer is None or layer.get("type") != "motif":
            continue
        lid = layer.get("id")
        attempted.add(lid)
        src_idx = spec.get("source_image_index")
        is_text = bool(spec.get("text"))
        try:
            if is_text:
                motif_id = _resolve_text_layer(spec, layer, resolved, sink)
            elif src_idx is not None:
                # Chat multimodal: this motif IS an uploaded image -> vectorize it.
                if (
                    isinstance(src_idx, bool)
                    or not isinstance(src_idx, int)
                    or not images
                    or not (0 <= src_idx < len(images))
                ):
                    raise AdapterClientError(f"invalid source_image_index {src_idx!r}")
                query_vec = embed_query(_descriptor_text(spec), client=embedding_client)
                motif_id = vectorize_via_recraft(
                    images[src_idx], spec, client=recraft_client, embedding=query_vec
                )
                _log_path("vectorize", spec, selected_id=motif_id)
            else:
                motif_id = _resolve_one(
                    spec,
                    store=store,
                    recraft_client=recraft_client,
                    embedding_client=embedding_client,
                    seed=seed,
                )
        except AdapterClientError:
            failed.add(lid)
            reasons[lid] = (
                "text could not be rendered as a motif"
                if is_text
                else f"uploaded image {src_idx} could not be vectorized as a motif"
                if src_idx is not None
                else f"{spec.get('subject', '?')}/{spec.get('scope', '?')}"
            )
            continue
        layer.setdefault("params", {})["motif_id"] = motif_id

    if not failed:
        return resolved

    # Spec §6.4: every attempted motif failed → 502 (no partial result is meaningful).
    if not (attempted - failed):
        raise AdapterClientError(
            f"all {len(attempted)} motif(s) failed the Tier-1 sanitize/structure gate"
        )

    # Cascade to a fixpoint: a layer hosting on a dropped layer can no longer compose.
    dropped = set(failed)
    while True:
        grew = False
        for layer in resolved.get("layers", []):
            lid = layer.get("id")
            if lid in dropped:
                continue
            host = (layer.get("placement") or {}).get("host_layer")
            if host in dropped:
                dropped.add(lid)
                reasons[lid] = f"host_layer {host!r}"
                grew = True
        if not grew:
            break

    survivors = [
        layer for layer in resolved.get("layers", []) if layer.get("id") not in dropped
    ]
    if not survivors:
        raise AdapterClientError("motif drop cascade left no composable layers")

    # Warn in layer order (deterministic), distinguishing gate failures from cascades.
    for layer in resolved.get("layers", []):
        lid = layer.get("id")
        if lid not in dropped:
            continue
        if lid in failed:
            sink.append(
                f"motif layer {lid!r} dropped after Tier-1 gate exhausted "
                f"({reasons[lid]})"
            )
        else:
            sink.append(
                f"layer {lid!r} dropped because its {reasons[lid]} was dropped"
            )
    resolved["layers"] = survivors
    return resolved
