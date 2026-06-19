"""Session-14 (P3) curation/promotion + catalog: status transition, curated-only pool
activation, rejection cache-invalidation, and head-catalog seeding.

All tests use an in-memory fake store (the MotifStore Protocol, now with the S14
set_status/delete/find_by_status methods) — psycopg is never imported.
"""

import dataclasses

import pytest

import app.adapters.llm as llm_adapter
import app.adapters.recraft as recraft_adapter
from app.engine import determinism
from app.motifs import store as store_mod
from app.motifs.facets import variant_group_key
from app.motifs.registry import (
    MOTIFS,
    normalize_motif_svg,
    promote_motif,
    register_motif,
    reject_motif,
)
from app.motifs.store import MotifRecord, MotifStoreNotConfigured, set_default_store
from scripts.seed_head_catalog import HEAD_CATALOG, seed


def _svg(inner: str, viewbox: str = "0 0 100 100") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{inner}</svg>'


class _FakeStore:
    """In-memory MotifStore including the S14 mutation methods. upsert models
    ON CONFLICT DO NOTHING; set_status replaces the frozen record with an updated copy."""

    def __init__(self, *records: MotifRecord) -> None:
        self.rows: dict[str, MotifRecord] = {r.id: r for r in records}

    def upsert(self, record: MotifRecord) -> None:
        self.rows.setdefault(record.id, record)

    def get(self, motif_id: str):
        return self.rows.get(motif_id)

    def all(self):
        return sorted(self.rows.values(), key=lambda r: r.id)

    def find_by_facets(self, subject, part):
        return sorted(
            (r for r in self.rows.values() if r.subject == subject and r.part == part),
            key=lambda r: r.id,
        )

    def find_by_variant_group(self, variant_group, *, status="curated"):
        return sorted(
            (
                r
                for r in self.rows.values()
                if r.variant_group == variant_group and r.status == status
            ),
            key=lambda r: r.id,
        )

    def find_by_status(self, status):
        return sorted(
            (r for r in self.rows.values() if r.status == status), key=lambda r: r.id
        )

    def set_status(self, motif_id, status):
        rec = self.rows.get(motif_id)
        if rec is not None:  # absent id is a no-op (mirrors UPDATE ... WHERE id=)
            self.rows[motif_id] = dataclasses.replace(rec, status=status)

    def delete(self, motif_id):
        self.rows.pop(motif_id, None)


@pytest.fixture(autouse=True)
def _clean():
    """Reset the process-wide store, the test-authored MOTIFS, and the motif-id caches
    that reject_motif flushes, around every test."""

    def _purge():
        store_mod.clear_default_store()
        llm_adapter.clear_motif_svg_cache()
        recraft_adapter.clear_motif_cache()
        recraft_adapter.clear_recraft_motif_cache()
        for key in [k for k in MOTIFS if k not in ("circle", "bee")]:
            del MOTIFS[key]

    _purge()
    yield
    _purge()


def _register(fake, inner, *, status="auto"):
    set_default_store(fake)
    motif = normalize_motif_svg(_svg(inner))
    return register_motif(motif, subject="flower", part="whole", status=status)


# --- status threading through register_motif --------------------------------


def test_register_persists_explicit_curated_status():
    fake = _FakeStore()
    mid = _register(fake, '<circle cx="50" cy="50" r="40" fill="#abc"/>', status="curated")
    assert fake.rows[mid].status == "curated"


def test_register_defaults_to_auto():
    fake = _FakeStore()
    mid = _register(fake, '<rect x="0" y="0" width="60" height="60" fill="#abc"/>')
    assert fake.rows[mid].status == "auto"


# --- AC#1: curated-only pool; auto excluded until promoted -------------------


def test_auto_excluded_from_pool_until_promoted():
    fake = _FakeStore()
    group = variant_group_key("flower", "whole")
    ids = [
        _register(fake, '<circle cx="50" cy="50" r="40" fill="#abc"/>'),
        _register(fake, '<rect x="10" y="10" width="60" height="60" fill="#abc"/>'),
    ]
    # auto rows must NOT enter the curated sampling pool (spec §7.4)
    assert fake.find_by_variant_group(group, status="curated") == []
    for mid in ids:
        promote_motif(mid)
    pool = [r.id for r in fake.find_by_variant_group(group, status="curated")]
    assert sorted(pool) == sorted(ids)


def test_promote_unconfigured_store_raises():
    # No default store (cleared by the fixture): curation is explicit, so it fails loud.
    with pytest.raises(MotifStoreNotConfigured):
        promote_motif("recraft-x")


# --- AC#3: rejection propagates across DB + in-memory + adapter caches -------


def test_reject_removes_from_store_memory_and_caches():
    fake = _FakeStore()
    mid = _register(fake, '<circle cx="50" cy="50" r="40" fill="#abc"/>')
    assert mid in MOTIFS and mid in fake.rows
    # warm the adapter caches with entries that map a spec/prompt to this motif id
    llm_adapter._motif_svg_cache["k"] = mid
    recraft_adapter._motif_cache["k"] = mid
    recraft_adapter._motif_svg_cache["k"] = mid

    reject_motif(mid)

    assert mid not in fake.rows  # DB row deleted
    assert mid not in MOTIFS  # in-memory registry evicted
    assert llm_adapter._motif_svg_cache == {}  # motif-id caches flushed (spec §6.4)
    assert recraft_adapter._motif_cache == {}
    assert recraft_adapter._motif_svg_cache == {}


def test_reject_builtin_is_refused():
    # The built-in guard fires before the store is resolved (no store configured here),
    # so a ValueError — not MotifStoreNotConfigured — proves the guard's precedence.
    with pytest.raises(ValueError):
        reject_motif("circle")
    assert "circle" in MOTIFS


# --- AC#2: head-catalog seed yields a curated pool >= 2 with seed diversity ---


def test_seed_creates_curated_pool_with_variant_diversity():
    fake = _FakeStore()
    ids = seed(fake)
    assert len(ids) == len(HEAD_CATALOG)
    assert all(fake.rows[i].status == "curated" for i in ids)  # seeded as curated

    group = variant_group_key("flower", "whole")
    pool = [r.id for r in fake.find_by_variant_group(group, status="curated")]
    assert len(pool) >= 2  # pool >= 2 demonstrated (variant sampling is now effective)

    # Seed-based selection varies across seeds (diversity) and is stable per seed (§7.1).
    chosen = {determinism.select_variant(pool, group, s) for s in range(20)}
    assert len(chosen) >= 2
    assert determinism.select_variant(pool, group, 3) == determinism.select_variant(
        pool, group, 3
    )
