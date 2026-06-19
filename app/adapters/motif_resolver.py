"""Deterministic motif-resolution glue (spec §6, P0).

The flow: ``prompt`` → LLM (``intent`` + ``motif_specs``) → **this module** → a concrete
``motif_id`` injected into each motif layer of the intent → engine ``compose``. The
selection is a *pure, deterministic* step; only the miss-path SVG generation calls the
LLM, and that is frozen by the adapter cache (so the determinism contract holds: the
engine only ever sees an intent with concrete motif ids).

P0 retrieval (D18, no embeddings): **exact descriptor match** → **subject/part hard
filter** → **generate-on-miss**. ``variant_group`` is ``hash(subject, part)`` in P0, so
the two lookup steps coincide; the embedding/τ soft-similarity stage lands in S11.
"""

from __future__ import annotations

import copy

from app.adapters.llm import generate_motif_svg
from app.motifs import facets
from app.motifs.store import MotifStoreError, get_default_store

# Facets that define an "exact descriptor" (controlled + light free facets, P0).
_EXACT_FACETS = ("subject", "part", "view", "expression", "style")


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


def _resolve_one(spec: dict, *, store, llm_client) -> str:
    # Normalize the controlled facets so the DB filter, the exact-match comparison, and
    # the generated motif's stored facets all agree (NFC + strip + casefold). Without
    # this a differently-cased subject ("Pig" vs "pig") would miss an existing motif and
    # regenerate, defeating reuse/dedup.
    subject = facets.normalize_facet(spec.get("subject"))
    part = facets.normalize_facet(spec.get("part"))
    if subject and part and store is not None:
        try:
            candidates = store.find_by_facets(subject, part)
        except MotifStoreError:
            # A flaky DB read is treated as a miss (graceful, spec §6.4): regeneration
            # is idempotent via the content-hash id, so correctness is preserved.
            candidates = []
        if candidates:
            exact = _exact_match(spec, candidates)
            if exact is not None:
                return exact
            # Hard-filter hit: reuse the lowest-id match deterministically (P0; τ is S11).
            # min() does not depend on the store returning rows in any particular order.
            return min(candidates, key=lambda rec: rec.id).id
    # Miss (or missing facets / no store) → generate a single-color motif via the LLM
    # (D8 simple=LLM). May raise AdapterClientError (→ 502) if no sanitizable SVG.
    return generate_motif_svg(spec, client=llm_client)


def resolve_motifs(
    intent: dict,
    motif_specs: list[dict],
    *,
    store=None,
    llm_client=None,
) -> dict:
    """Return a copy of ``intent`` with each motif layer's ``params.motif_id`` resolved.

    Each spec is matched to a layer by ``layer_id``: exact descriptor match → subject/
    part hard-filter hit → generate-on-miss. Layers without a matching spec are left
    untouched (a direct built-in reference, or the legacy/image path where
    ``motif_specs`` is empty makes this a no-op).
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
        motif_id = _resolve_one(spec, store=store, llm_client=llm_client)
        layer.setdefault("params", {})["motif_id"] = motif_id
    return resolved
