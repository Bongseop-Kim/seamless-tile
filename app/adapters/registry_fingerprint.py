"""Derive ``registry_version`` from the live curated motif pool (spec §7.3/D17).

The seal "(prompt, seed, registry_version) -> same result" only holds if the version
moves when the curated sampling pool changes. The pool is mutable global DB state
(promotion via the curation CLI), so a static constant cannot track it. This helper
fingerprints the sorted curated motif ids at request time, so the stamp and the pool
move together atomically.

Lives in the adapter layer (where the store is reachable); the engine stays
store-agnostic and merely receives the computed string.
"""

from __future__ import annotations

from app.core.config import REGISTRY_VERSION
from app.engine.determinism import stable_hash
from app.motifs.store import MotifStore, MotifStoreError


def registry_version_for(store: MotifStore | None) -> str:
    """Return ``REGISTRY_VERSION`` plus a fingerprint of the curated pool.

    Baseline ``REGISTRY_VERSION`` (no suffix) is returned when the pool is empty, the
    store is absent, or the DB query fails -- so the degenerate pool stamps the exact
    value the old static constant did, and a store outage degrades like ``_select_variant``
    (empty pool) rather than failing the request. A non-empty curated pool yields
    ``f"{REGISTRY_VERSION}+pool.{hex8}"`` where ``hex8`` is the leading 8 hex digits of the
    sha256 over the sorted curated ids. Pure function of the pool contents: no time,
    randomness, or store-order dependence (ids are re-sorted here).
    """
    if store is None:
        return REGISTRY_VERSION
    try:
        records = store.find_by_status("curated")
    except MotifStoreError:
        return REGISTRY_VERSION
    pool_ids = sorted(rec.id for rec in records)
    if not pool_ids:
        return REGISTRY_VERSION
    hex8 = format(stable_hash("\n".join(pool_ids)), "064x")[:8]
    return f"{REGISTRY_VERSION}+pool.{hex8}"
