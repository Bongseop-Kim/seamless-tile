"""Deterministic motif-resolution glue (spec §6, P0 + S11 soft similarity).

The flow: ``prompt`` → LLM (``intent`` + ``motif_specs``) → **this module** → a concrete
``motif_id`` injected into each motif layer of the intent → engine ``compose``. The
selection is a *pure, deterministic* step; the non-deterministic pieces (embedding
search, miss-path SVG generation) are frozen by adapter caches, so the determinism
contract holds: the engine only ever sees an intent with concrete motif ids.

Retrieval (spec §6.1, D18): **exact descriptor match** → **subject/part hard filter** →
**embedding soft similarity (τ gate)** → **generate-on-miss**. Every hit routes through
the variant_group's curated sampling pool (§7.1); when that pool is empty (degenerate
until S14 curation), it falls back to the matched motif. The embedding stage is
fail-soft: if no embedding client is configured, or the call fails, or no candidate has
a comparable embedding, it degrades to the S10 lowest-id hard-filter reuse.
"""

from __future__ import annotations

import copy

import numpy as np

from app.adapters.base import AdapterClientError
from app.adapters.embedding import embed_query
from app.adapters.llm import generate_motif_svg
from app.core.config import get_settings
from app.engine import determinism
from app.motifs import facets
from app.motifs.store import MotifStoreError, get_default_store

# Facets that define an "exact descriptor" (controlled + light free facets, P0).
_EXACT_FACETS = ("subject", "part", "view", "expression", "style")


def _tau() -> float:
    """Cosine similarity threshold for "reuse vs generate" (spec §6.1/D13)."""
    return get_settings().motif_similarity_tau


def _descriptor_text(spec: dict) -> str:
    """Embedding source text for a spec (D12: a normalized English descriptor).

    Prefers an explicit ``description``; otherwise synthesizes one from the facets with a
    FIXED algorithm so two implementations produce the same string: empty facets are
    dropped, tokens are single-spaced, and there are no dangling commas.
    """
    description = (spec.get("description") or "").strip()
    if description:
        return description
    subject = (spec.get("subject") or "").strip()
    part = (spec.get("part") or "").strip()
    expression = (spec.get("expression") or "").strip()
    style = (spec.get("style") or "").strip()
    view = (spec.get("view") or "").strip()
    head = " ".join(t for t in (expression, subject, part) if t)
    view_clause = f"{view} view" if view else ""
    return ", ".join(part_ for part_ in (head, view_clause, style) if part_)


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


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _best_by_similarity(candidates: list, query_vec: list[float] | None):
    """The (record, cosine) with the highest similarity to ``query_vec``, else ``None``.

    ``None`` means soft similarity is unavailable (no query vector, or no candidate has a
    same-dimension embedding) — the caller then falls back to S10 behavior. Candidates
    are scanned in id order with a strict ``>`` so ties keep the lowest id (matching the
    exact/hard-filter convention; spec §9.7).
    """
    if query_vec is None:
        return None
    q = np.asarray(query_vec, dtype=float)
    best = None  # (rec, sim)
    for rec in sorted(candidates, key=lambda r: r.id):
        emb = rec.embedding
        if not emb or len(emb) != len(query_vec):  # dimension guard (model/legacy skew)
            continue
        sim = _cosine(q, np.asarray(emb, dtype=float))
        if best is None or sim > best[1]:
            best = (rec, sim)
    return best


def _select_variant(store, variant_group, seed: int, fallback_id: str) -> str:
    """Seed-sample one variant from the group's curated pool (§7.1), else ``fallback_id``.

    The pool is curated-only (§7.4); when it is empty (degenerate until S14 curation) the
    matched motif itself is returned, so S11 hits resolve to the matched id.
    """
    if not variant_group:
        return fallback_id
    try:
        pool = [
            rec.id
            for rec in store.find_by_variant_group(variant_group, status="curated")
        ]
    except MotifStoreError:
        pool = []
    if not pool:
        return fallback_id
    return determinism.select_variant(pool, variant_group, seed)


def _resolve_one(spec: dict, *, store, llm_client, embedding_client, seed: int) -> str:
    # Normalize the controlled facets so the DB filter, the exact-match comparison, and
    # the generated motif's stored facets all agree (NFC + strip + casefold).
    subject = facets.normalize_facet(spec.get("subject"))
    part = facets.normalize_facet(spec.get("part"))
    query_vec: list[float] | None = None
    if subject and part and store is not None:
        try:
            candidates = store.find_by_facets(subject, part)
        except MotifStoreError:
            # A flaky DB read is treated as a miss (graceful, spec §6.4): regeneration is
            # idempotent via the content-hash id, so correctness is preserved.
            candidates = []
        if candidates:
            # (0) Exact descriptor match wins (D18); route through the group's pool.
            exact = _exact_match(spec, candidates)
            if exact is not None:
                rec = next(c for c in candidates if c.id == exact)
                return _select_variant(store, rec.variant_group, seed, exact)
            # (2) Soft similarity. Fail-soft: embed errors degrade like a flaky read.
            try:
                query_vec = embed_query(_descriptor_text(spec), client=embedding_client)
            except AdapterClientError:
                query_vec = None
            best = _best_by_similarity(candidates, query_vec)
            if best is None:
                # Embedding unavailable / no comparable candidate → S10 hard-filter reuse
                # (reuse-first, lowest id), routed through the variant pool.
                fallback = min(candidates, key=lambda c: c.id)
                return _select_variant(store, fallback.variant_group, seed, fallback.id)
            rec, sim = best
            if sim >= _tau():  # τ or above → reuse (hit)
                return _select_variant(store, rec.variant_group, seed, rec.id)
            # below τ → miss (generate); fall through.
    # Miss (or missing facets / no store) → generate a single-color motif via the LLM
    # (D8 simple=LLM), persisting the query embedding so future requests can soft-match.
    # May raise AdapterClientError (→ 502) if no sanitizable SVG.
    return generate_motif_svg(spec, client=llm_client, embedding=query_vec)


def resolve_motifs(
    intent: dict,
    motif_specs: list[dict],
    *,
    store=None,
    llm_client=None,
    embedding_client=None,
    seed: int = 0,
) -> dict:
    """Return a copy of ``intent`` with each motif layer's ``params.motif_id`` resolved.

    Each spec is matched to a layer by ``layer_id``: exact descriptor match → subject/
    part hard-filter → embedding soft similarity (τ) → generate-on-miss, with hits
    seed-sampled from the curated variant pool. ``seed`` must be the SAME effective seed
    the engine composes with (the route unifies it) so variant selection and composition
    agree. Layers without a matching spec are left untouched.
    """
    if not motif_specs:
        return intent
    if store is None:
        store = get_default_store()

    resolved = copy.deepcopy(intent)
    layers_by_id = {
        layer.get("id"): layer
        for layer in resolved.get("layers", [])
        if isinstance(layer, dict)
    }
    for spec in motif_specs:
        layer = layers_by_id.get(spec.get("layer_id"))
        if layer is None or layer.get("type") != "motif":
            continue
        motif_id = _resolve_one(
            spec,
            store=store,
            llm_client=llm_client,
            embedding_client=embedding_client,
            seed=seed,
        )
        layer.setdefault("params", {})["motif_id"] = motif_id
    return resolved
