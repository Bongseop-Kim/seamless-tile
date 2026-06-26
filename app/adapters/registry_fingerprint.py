"""Derive ``registry_version`` from the live reusable motif pool (spec §7.3/D17).

The seal "(prompt, seed, registry_version) -> same result" only holds if the version
moves when the reusable sampling pool changes. The pool is mutable DB state, so a static
constant cannot track it. This helper fingerprints the sorted reusable motif ids at
request time, so the stamp and the pool move together atomically.

Lives in the adapter layer (where the store is reachable); the engine stays
store-agnostic and merely receives the computed string.
"""

from __future__ import annotations

from app.core.config import REGISTRY_VERSION
from app.engine.determinism import stable_hash
from app.motifs.registry import registry_pool_epoch
from app.motifs.store import MotifStore, MotifStoreError

# Process-local memo of the last fingerprint: (store, epoch, version). The store is a
# boot-installed singleton, so this collapses the per-request pool query to a
# single DB round-trip per pool change (audit C1). Keyed by object identity (`is`, not
# id(): a strong ref here prevents id-recycling false hits across short-lived test stores)
# plus the registry_pool_epoch bumped on every register/delete.
_cache: tuple[object, int, str] | None = None


def registry_version_for(store: MotifStore | None) -> str:
    """Return ``REGISTRY_VERSION`` plus a fingerprint of the reusable pool.

    Baseline ``REGISTRY_VERSION`` (no suffix) is returned when the pool is empty, the
    store is absent, or the DB query fails -- so the degenerate pool stamps the exact
    value the old static constant did, and a store outage degrades like ``_select_variant``
    (empty pool) rather than failing the request. A non-empty reusable pool yields
    ``f"{REGISTRY_VERSION}+pool.{hex8}"`` where ``hex8`` is the leading 8 hex digits of the
    sha256 over the sorted motif ids. Pure function of the pool contents: no time,
    randomness, or store-order dependence (ids are re-sorted here).

    The result is memoized per (store, reusable-pool epoch); a transient store error
    degrades to baseline without caching, so the next request retries.
    """
    if store is None:
        return REGISTRY_VERSION
    global _cache
    epoch = registry_pool_epoch()
    if _cache is not None and _cache[0] is store and _cache[1] == epoch:
        return _cache[2]
    try:
        pool_ids = sorted(store.all_ids())
    except MotifStoreError:
        return REGISTRY_VERSION  # transient outage: degrade, do not cache
    if not pool_ids:
        version = REGISTRY_VERSION
    else:
        hex8 = format(stable_hash("\n".join(pool_ids)), "064x")[:8]
        version = f"{REGISTRY_VERSION}+pool.{hex8}"
    _cache = (store, epoch, version)
    return version
