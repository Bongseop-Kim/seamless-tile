"""Reusable motif pool activation, cleanup, and head-catalog seeding."""

import pytest

import app.adapters.recraft as recraft_adapter
from app.engine import determinism
from app.motifs import store as store_mod
from app.motifs.facets import variant_group_key
from app.motifs.registry import (
    BUILTIN_MOTIF_IDS,
    MOTIFS,
    delete_motif,
    normalize_motif_svg,
    register_motif,
)
from app.motifs.store import MotifRecord, set_default_store
from scripts.seed_head_catalog import HEAD_CATALOG, seed

from tests._helpers import _svg
from tests.test_intent import _register_test_motifs


class _FakeStore:
    """In-memory MotifStore. upsert models ON CONFLICT DO NOTHING."""

    def __init__(self, *records: MotifRecord) -> None:
        self.rows: dict[str, MotifRecord] = {r.id: r for r in records}

    def upsert(self, record: MotifRecord) -> None:
        self.rows.setdefault(record.id, record)

    def get(self, motif_id: str):
        return self.rows.get(motif_id)

    def all(self):
        return sorted(self.rows.values(), key=lambda r: r.id)

    def find_by_variant_group(self, variant_group):
        return sorted(
            (r for r in self.rows.values() if r.variant_group == variant_group),
            key=lambda r: r.id,
        )

    def delete(self, motif_id):
        self.rows.pop(motif_id, None)


@pytest.fixture(autouse=True)
def _clean():
    """Reset store, test-authored MOTIFS, and motif-id caches around every test."""

    def _purge():
        store_mod.clear_default_store()
        recraft_adapter.clear_motif_cache()
        recraft_adapter.clear_recraft_motif_cache()
        for key in [k for k in MOTIFS if k not in BUILTIN_MOTIF_IDS]:
            del MOTIFS[key]
        # circle/bee are test fixtures (no longer built-ins), so the purge above evicts
        # them; re-seed so cross-file tests relying on them still find them.
        _register_test_motifs()

    _purge()
    yield
    _purge()


def _register(fake, inner):
    set_default_store(fake)
    motif = normalize_motif_svg(_svg(inner))
    return register_motif(motif, subject="flower", scope="whole")


def test_registered_motifs_enter_pool_immediately():
    fake = _FakeStore()
    group = variant_group_key("flower", "whole")
    ids = [
        _register(fake, '<circle cx="50" cy="50" r="40" fill="#abc"/>'),
        _register(fake, '<rect x="10" y="10" width="60" height="60" fill="#abc"/>'),
    ]

    pool = [r.id for r in fake.find_by_variant_group(group)]
    assert sorted(pool) == sorted(ids)


def test_delete_removes_from_store_memory_and_caches():
    fake = _FakeStore()
    mid = _register(fake, '<circle cx="50" cy="50" r="40" fill="#abc"/>')
    assert mid in MOTIFS and mid in fake.rows
    recraft_adapter._motif_cache["k"] = mid
    recraft_adapter._motif_svg_cache["k"] = mid

    delete_motif(mid)

    assert mid not in fake.rows
    assert mid not in MOTIFS
    assert recraft_adapter._motif_cache == {}
    assert recraft_adapter._motif_svg_cache == {}


def test_seed_creates_reusable_pool_with_variant_diversity():
    fake = _FakeStore()
    ids = seed(fake)
    assert len(ids) == len(HEAD_CATALOG)

    group = variant_group_key("flower", "whole")
    pool = [r.id for r in fake.find_by_variant_group(group)]
    assert len(pool) >= 2

    chosen = {determinism.select_variant(pool, group, s) for s in range(20)}
    assert len(chosen) >= 2
    assert determinism.select_variant(pool, group, 3) == determinism.select_variant(
        pool, group, 3
    )
