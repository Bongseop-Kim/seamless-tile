"""``registry_version_for``: derive the repro seal from the curated pool (spec §7.3/D17).

Pure unit tests over an in-memory fake store (psycopg never imported). The helper must
degrade to the baseline constant when there is nothing to fingerprint, and otherwise be a
deterministic, order-independent function of the curated motif ids only.
"""

from app.adapters.registry_fingerprint import registry_version_for
from app.core.config import REGISTRY_VERSION
from app.motifs.registry import _bump_curated_pool_epoch
from app.motifs.store import MotifRecord, MotifStoreError


def _rec(motif_id: str, status: str = "curated") -> MotifRecord:
    return MotifRecord(
        id=motif_id,
        symbol="<symbol/>",
        bbox_mm=(0.0, 0.0, 10.0, 10.0),
        anchor=(0.0, 0.0),
        status=status,
    )


class _FakeStore:
    """Minimal MotifStore: only ``find_by_status`` is exercised by the helper."""

    def __init__(self, *records: MotifRecord) -> None:
        self.rows = {r.id: r for r in records}

    def find_by_status(self, status: str) -> list[MotifRecord]:
        return sorted(
            (r for r in self.rows.values() if r.status == status), key=lambda r: r.id
        )


class _ErroringStore:
    def find_by_status(self, status: str) -> list[MotifRecord]:
        raise MotifStoreError("simulated DB outage")


def test_no_store_returns_baseline():
    assert registry_version_for(None) == REGISTRY_VERSION


def test_empty_curated_pool_returns_baseline():
    # Degenerate S11 pool: nothing curated yet -> byte-identical to the old constant.
    store = _FakeStore(_rec("a", status="auto"), _rec("b", status="auto"))
    assert registry_version_for(store) == REGISTRY_VERSION


def test_store_error_degrades_to_baseline():
    # Mirrors `_select_variant`: a store outage degrades, it does not fail the request.
    assert registry_version_for(_ErroringStore()) == REGISTRY_VERSION


def test_curated_pool_yields_suffixed_version():
    version = registry_version_for(_FakeStore(_rec("m1"), _rec("m2")))
    assert version.startswith(f"{REGISTRY_VERSION}+pool.")
    suffix = version.split("+pool.", 1)[1]
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def test_different_curated_sets_differ():
    v1 = registry_version_for(_FakeStore(_rec("m1"), _rec("m2")))
    v2 = registry_version_for(_FakeStore(_rec("m1"), _rec("m3")))
    assert v1 != v2
    assert v1.startswith(f"{REGISTRY_VERSION}+pool.")
    assert v2.startswith(f"{REGISTRY_VERSION}+pool.")


def test_order_independent_and_deterministic():
    a = _FakeStore(_rec("m1"), _rec("m2"), _rec("m3"))
    b = _FakeStore(_rec("m3"), _rec("m1"), _rec("m2"))
    assert registry_version_for(a) == registry_version_for(b)
    assert registry_version_for(a) == registry_version_for(a)


def test_auto_rows_excluded_from_fingerprint():
    base = registry_version_for(_FakeStore(_rec("m1"), _rec("m2")))
    with_auto = registry_version_for(
        _FakeStore(_rec("m1"), _rec("m2"), _rec("z9", status="auto"))
    )
    assert base == with_auto


class _CountingStore(_FakeStore):
    """Tracks how many times the curated pool was queried (cache-hit verification)."""

    def __init__(self, *records: MotifRecord) -> None:
        super().__init__(*records)
        self.calls = 0

    def find_by_status(self, status: str) -> list[MotifRecord]:
        self.calls += 1
        return super().find_by_status(status)


def test_result_is_memoized_per_store_and_epoch():
    # C1: a steady curated pool collapses to a single DB round-trip across requests.
    store = _CountingStore(_rec("m1"), _rec("m2"))
    first = registry_version_for(store)
    second = registry_version_for(store)
    assert first == second
    assert store.calls == 1  # second call served from cache


def test_curation_epoch_bump_invalidates_cache():
    # C1: promote/reject bump the epoch, forcing a recompute on the next request.
    store = _CountingStore(_rec("m1"), _rec("m2"))
    registry_version_for(store)
    assert store.calls == 1
    _bump_curated_pool_epoch()  # stands in for promote_motif / reject_motif
    registry_version_for(store)
    assert store.calls == 2
