"""Deterministic motif-resolution glue (spec §6, P0 + S11 soft similarity).

The flow: ``prompt`` → LLM (``intent`` + ``motif_specs``) → **this module** → a concrete
``motif_id`` injected into each motif layer of the intent → engine ``compose``. The
selection is a *pure, deterministic* step; the non-deterministic pieces (embedding
search, miss-path SVG generation) are frozen by adapter caches, so the determinism
contract holds: the engine only ever sees an intent with concrete motif ids.

Retrieval (spec §6.1, D18): **exact descriptor match** → **scope hard filter** →
**embedding soft similarity (τ gate)** → **generate-on-miss**. Every hit routes through
the variant_group's reusable sampling pool (§7.1); when that pool is empty, it falls
back to the matched motif. The embedding stage is fail-soft: if no embedding client is
configured, or the call fails, or no candidate has a comparable embedding, it degrades
to the S10 lowest-id hard-filter reuse.
"""

from __future__ import annotations

import copy

from app.adapters.base import AdapterClientError
from app.adapters.embedding import embed_query
from app.adapters.recraft import generate_via_recraft
from app.core.config import get_settings
from app.core.observability import log_metrics
from app.engine import determinism
from app.motifs import facets
from app.motifs.store import MotifStoreError, get_default_store

# Facets that define an "exact descriptor" (subject + scope + light free facets, P0).
_EXACT_FACETS = ("subject", "scope", "view", "expression", "style")


def _tau() -> float:
    """Cosine similarity threshold for "reuse vs generate" (spec §6.1/D13)."""
    return get_settings().motif_similarity_tau


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


def _select_variant(store, variant_group, seed: int, fallback_id: str) -> str:
    """Seed-sample one variant from the group's reusable pool (§7.1), else ``fallback_id``.

    When it is empty, the matched motif itself is returned, so S11 hits resolve to the
    matched id.
    """
    if not variant_group:
        return fallback_id
    try:
        pool = [rec.id for rec in store.find_by_variant_group(variant_group)]
    except MotifStoreError:
        pool = []
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
            # (0) Exact descriptor match wins (D18); route through the group's pool.
            exact = _exact_match(spec, candidates)
            if exact is not None:
                rec = next(c for c in candidates if c.id == exact)
                selected = _select_variant(store, rec.variant_group, seed, exact)
                _log_path("exact", spec, selected_id=selected)
                return selected
            # (2) Soft similarity, ranked in Postgres. Fail-soft: embed/DB errors degrade
            # like a flaky read.
            try:
                query_vec = embed_query(_descriptor_text(spec), client=embedding_client)
            except AdapterClientError:
                query_vec = None
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
                    store, fallback.variant_group, seed, fallback.id
                )
                _log_path("fallback", spec, selected_id=selected)
                return selected
            best_sim = match.similarity
            if best_sim >= _tau():  # τ or above → reuse (hit)
                selected = _select_variant(store, match.variant_group, seed, match.id)
                _log_path("vector", spec, similarity=best_sim, selected_id=selected)
                return selected
            # below τ → miss (generate); fall through.
    # Miss (or missing facets / no store) → generate via Recraft, persisting the query
    # embedding so future requests can soft-match. May raise AdapterClientError (→ 502)
    # if the generated SVG is unsanitizable or no Recraft client is configured.
    new_id = generate_via_recraft(spec, client=recraft_client, embedding=query_vec)
    _log_path("generate", spec, similarity=best_sim, selected_id=new_id)
    return new_id


def resolve_motifs(
    intent: dict,
    motif_specs: list[dict],
    *,
    store=None,
    recraft_client=None,
    embedding_client=None,
    seed: int = 0,
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
        try:
            motif_id = _resolve_one(
                spec,
                store=store,
                recraft_client=recraft_client,
                embedding_client=embedding_client,
                seed=seed,
            )
        except AdapterClientError:
            failed.add(lid)
            reasons[lid] = f"{spec.get('subject', '?')}/{spec.get('scope', '?')}"
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
