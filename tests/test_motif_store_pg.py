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

pytestmark = pytest.mark.skipif(
    get_settings().supabase_db_url is None,
    reason="no SUPABASE_DB_URL configured (live Supabase integration test)",
)

_TEST_ID = "recraft-pgtest000001"


def _delete(store: PostgresMotifStore, motif_id: str) -> None:
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM motifs WHERE id = %s", (motif_id,))


def test_upsert_get_roundtrip_and_idempotent():
    store = PostgresMotifStore(get_settings().supabase_db_url)
    record = MotifRecord(
        id=_TEST_ID,
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

        got = store.get(_TEST_ID)
        assert got is not None
        assert got.id == _TEST_ID
        assert got.bbox_mm == (-0.5, -0.5, 0.5, 0.5)
        assert got.anchor == (0.0, 0.0)
        assert got.color_slots == ["s0"]
        assert got.tags == ["cute", "baby"]
        assert got.subject == "pig"
        assert got.part == "face"
        assert got.view == "front"

        # also assert idempotency at the DB layer: exactly one row for this id
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM motifs WHERE id = %s", (_TEST_ID,))
            assert cur.fetchone()[0] == 1
    finally:
        _delete(store, _TEST_ID)


def test_upsert_get_roundtrip_embedding():
    # pgvector stores float4, so the round-trip is NOT bit-identical: compare with a
    # tolerance. NULL embeddings round-trip to None.
    store = PostgresMotifStore(get_settings().supabase_db_url)
    vec = [0.1, 0.2, 0.3, -0.5]
    record = MotifRecord(
        id=_TEST_ID,
        symbol='<symbol id="motif-x" overflow="visible"><circle r="0.5"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject="pig",
        part="face",
        embedding=vec,
    )
    try:
        store.upsert(record)
        got = store.get(_TEST_ID)
        assert got is not None
        assert got.embedding == pytest.approx(vec, rel=1e-6)
    finally:
        _delete(store, _TEST_ID)
