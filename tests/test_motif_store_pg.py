"""Session-9 live Supabase/Postgres round-trip (skipped unless SUPABASE_DB_URL is set).

Mirrors the renderer skip gate (test_api_export.py): with no DSN configured this whole
module is skipped, so default CI/local `pytest` stays green without a database. When a
DSN is present (and the `motifs` schema — owned by the React monorepo's migrations,
see ARCHITECTURE.md "영속화" — exists), it proves the psycopg store round-trips a record
through jsonb/text[] and that upsert is idempotent.
"""

import pytest

from app.core.config import get_settings
from app.motifs.store import MotifRecord, PostgresMotifStore


def _has_supabase_db_url() -> bool:
    dsn = get_settings().supabase_db_url
    return bool(dsn and dsn.strip())


pytestmark = pytest.mark.skipif(
    not _has_supabase_db_url(),
    reason="no SUPABASE_DB_URL configured (live Supabase integration test)",
)

_TEST_ID_ROUNDTRIP = "recraft-pgtest-roundtrip"
_TEST_ID_EMBEDDING = "recraft-pgtest-embedding"
_TEST_ID_STATUS = "recraft-pgtest-status"


def _delete(store: PostgresMotifStore, motif_id: str) -> None:
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM motifs WHERE id = %s", (motif_id,))


def test_upsert_get_roundtrip_and_idempotent():
    store = PostgresMotifStore(get_settings().supabase_db_url)
    record = MotifRecord(
        id=_TEST_ID_ROUNDTRIP,
        symbol='<symbol id="motif-x" overflow="visible"><circle r="0.5"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject="pig",
        part="face",
        view="front",  # exercises the `view` column (a non-reserved keyword) round-trip
        tags=["cute", "baby"],
        variant_group="abc123",
    )
    try:
        store.upsert(record)
        store.upsert(record)  # ON CONFLICT DO NOTHING => still one row

        got = store.get(_TEST_ID_ROUNDTRIP)
        assert got is not None
        assert got.id == _TEST_ID_ROUNDTRIP
        assert got.bbox_mm == (-0.5, -0.5, 0.5, 0.5)
        assert got.anchor == (0.0, 0.0)
        assert got.color_slots == ["s0"]
        assert got.tags == ["cute", "baby"]
        assert got.subject == "pig"
        assert got.part == "face"
        assert got.view == "front"

        # also assert idempotency at the DB layer: exactly one row for this id
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM motifs WHERE id = %s", (_TEST_ID_ROUNDTRIP,))
            assert cur.fetchone()[0] == 1
    finally:
        _delete(store, _TEST_ID_ROUNDTRIP)


def test_upsert_get_roundtrip_embedding():
    # pgvector stores float4, so the round-trip is NOT bit-identical: compare with a
    # tolerance. NULL embeddings round-trip to None.
    store = PostgresMotifStore(get_settings().supabase_db_url)
    vec = [0.1, 0.2, 0.3, -0.5]
    record = MotifRecord(
        id=_TEST_ID_EMBEDDING,
        symbol='<symbol id="motif-x" overflow="visible"><circle r="0.5"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject="pig",
        part="face",
        embedding=vec,
    )
    try:
        store.upsert(record)
        got = store.get(_TEST_ID_EMBEDDING)
        assert got is not None
        assert got.embedding == pytest.approx(vec, rel=1e-6)
    finally:
        _delete(store, _TEST_ID_EMBEDDING)


def test_set_status_delete_and_find_by_status_roundtrip():
    # S14: promotion (auto -> curated), the review queue (find_by_status), and rejection
    # (delete) round-trip through Postgres.
    store = PostgresMotifStore(get_settings().supabase_db_url)
    record = MotifRecord(
        id=_TEST_ID_STATUS,
        symbol='<symbol id="motif-x" overflow="visible"><circle r="0.5"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject="pig",
        part="face",
        variant_group="abc123",
    )  # status defaults to 'auto'
    try:
        store.upsert(record)
        # find_by_status: the new row is in the 'auto' review queue, not 'curated'.
        assert _TEST_ID_STATUS in {r.id for r in store.find_by_status("auto")}
        assert _TEST_ID_STATUS not in {r.id for r in store.find_by_status("curated")}

        # set_status: promote auto -> curated.
        store.set_status(_TEST_ID_STATUS, "curated")
        got = store.get(_TEST_ID_STATUS)
        assert got is not None and got.status == "curated"
        assert _TEST_ID_STATUS in {r.id for r in store.find_by_status("curated")}

        # delete: the row is gone.
        store.delete(_TEST_ID_STATUS)
        assert store.get(_TEST_ID_STATUS) is None
    finally:
        _delete(store, _TEST_ID_STATUS)
