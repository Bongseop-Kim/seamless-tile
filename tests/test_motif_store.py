"""Session-9 motif persistence: store seam + registry write-through / lazy / hydrate.

All tests use an in-memory fake store (the MotifStore Protocol) — psycopg is never
imported. The in-memory MOTIFS dict stays the source of truth; the store is exercised
through register_motif (write-through), get_motif (cold-miss lazy load) and
hydrate_from_store (boot).
"""

import pytest

from app.motifs import store as store_mod
from app.motifs.registry import (
    MOTIFS,
    get_motif,
    hydrate_from_store,
    normalize_motif_svg,
    register_motif,
)
from app.motifs.store import MotifRecord, set_default_store


def _svg(inner: str, viewbox: str = "0 0 100 100") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{inner}</svg>'


class _FakeStore:
    """In-memory MotifStore. upsert() models ON CONFLICT DO NOTHING and counts calls."""

    def __init__(self) -> None:
        self.rows: dict[str, MotifRecord] = {}
        self.upserts = 0

    def upsert(self, record: MotifRecord) -> None:
        self.upserts += 1
        self.rows.setdefault(record.id, record)  # DO NOTHING semantics

    def get(self, motif_id: str):
        return self.rows.get(motif_id)

    def all(self):
        return sorted(self.rows.values(), key=lambda r: r.id)


class _BoomStore:
    """Every operation fails — exercises the graceful-failure paths."""

    def upsert(self, record: MotifRecord) -> None:
        raise RuntimeError("db down")

    def get(self, motif_id: str):
        raise RuntimeError("db down")

    def all(self):
        raise RuntimeError("db down")


@pytest.fixture(autouse=True)
def _clean():
    # set_default_store is process-wide: reset it (and test-authored motifs) around
    # every test so persistence state never leaks into the determinism/recraft suites.
    def _purge():
        store_mod.clear_default_store()
        for key in [k for k in MOTIFS if k.startswith("recraft-")]:
            del MOTIFS[key]

    _purge()
    yield
    _purge()


def test_register_writes_through_to_store():
    fake = _FakeStore()
    set_default_store(fake)
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    motif_id = register_motif(motif, subject="dot", part="whole")

    assert motif_id in fake.rows
    rec = fake.rows[motif_id]
    assert rec.variant_group  # non-null, computed from facets
    assert rec.subject == "dot"
    assert rec.part == "whole"
    assert rec.symbol == motif.symbol


def test_reregister_same_svg_idempotent():
    fake = _FakeStore()
    set_default_store(fake)
    svg = _svg('<circle cx="50" cy="50" r="40" fill="#abc"/>')
    id1 = register_motif(normalize_motif_svg(svg))
    id2 = register_motif(normalize_motif_svg(svg))

    assert id1 == id2
    assert fake.upserts == 2  # both writes attempted
    assert len(fake.rows) == 1  # ON CONFLICT DO NOTHING => exactly one row


def test_restart_simulation_lazy_load():
    fake = _FakeStore()
    set_default_store(fake)
    motif = normalize_motif_svg(
        _svg('<rect x="0" y="0" width="10" height="10" fill="#abc"/>')
    )
    motif_id = register_motif(motif)

    del MOTIFS[motif_id]  # simulate a fresh process: empty cache, store retains the row
    loaded = get_motif(motif_id)

    assert loaded.id == motif_id
    assert loaded.symbol == motif.symbol
    assert loaded.bbox_mm == motif.bbox_mm
    assert loaded.anchor == motif.anchor
    assert motif_id in MOTIFS  # re-cached for subsequent lookups


def test_restart_simulation_boot_hydrate():
    fake = _FakeStore()
    set_default_store(fake)
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    motif_id = register_motif(motif)

    del MOTIFS[motif_id]
    count = hydrate_from_store(fake)

    assert count == 1
    assert motif_id in MOTIFS
    assert "circle" in MOTIFS and "bee" in MOTIFS  # built-ins untouched


def test_register_unconfigured_is_noop():
    # No default store (cleared by the autouse fixture).
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    motif_id = register_motif(motif)  # must not raise
    assert motif_id in MOTIFS


def test_get_motif_unconfigured_miss_raises_valueerror():
    with pytest.raises(ValueError):
        get_motif("recraft-doesnotexist")


def test_write_through_swallows_store_error():
    set_default_store(_BoomStore())
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    motif_id = register_motif(motif)  # store error must be swallowed
    assert motif_id in MOTIFS


def test_register_rejects_out_of_vocab_part():
    # A controlled-vocab violation is a caller bug: it must propagate (not be swallowed
    # like a DB error) and must not register the motif. Holds with no store configured.
    motif = normalize_motif_svg(_svg('<circle cx="50" cy="50" r="40" fill="#abc"/>'))
    with pytest.raises(ValueError):
        register_motif(motif, part="banana")
    assert motif.id not in MOTIFS  # validation happens before the registry mutation
