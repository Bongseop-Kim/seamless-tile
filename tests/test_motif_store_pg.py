"""Session-9 live Supabase/Postgres round-trip (explicit opt-in only).

Default CI/local `pytest` skips this module, even when `.env` contains a DSN. When
`RUN_LIVE_SUPABASE_TESTS=1` and `SUPABASE_DB_URL` are both set in the process
environment (not just `.env`), it proves the psycopg store round-trips a record
through jsonb/text[] and that upsert is idempotent.
"""

import os

import pytest

from app.core.config import get_settings
from app.motifs.store import MotifRecord, PostgresMotifStore


def _live_supabase_enabled() -> bool:
    dsn = os.environ.get("SUPABASE_DB_URL")
    enabled = os.environ.get("RUN_LIVE_SUPABASE_TESTS") == "1"
    return enabled and bool(dsn and dsn.strip())


def _store() -> PostgresMotifStore:
    dsn = get_settings().supabase_db_url
    assert dsn and dsn.strip()
    return PostgresMotifStore(dsn)


pytestmark = [
    pytest.mark.live_db,
    pytest.mark.skipif(
        not _live_supabase_enabled(),
        reason="set RUN_LIVE_SUPABASE_TESTS=1 and SUPABASE_DB_URL for live Supabase tests",
    ),
]

_TEST_ID_ROUNDTRIP = "recraft-pgtest-roundtrip"
_TEST_ID_EMBEDDING = "recraft-pgtest-embedding"
_TEST_ID_POOL = "recraft-pgtest-pool"
_TEST_VEC_PREFIX = "recraft-pgtest-vec-"


def _delete(store: PostgresMotifStore, motif_id: str) -> None:
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM motifs WHERE id = %s", (motif_id,))


def _py_cosine(a: list[float], b: list[float]) -> float:
    import math

    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _vec_record(suffix: str, scope: str, embedding: list[float]) -> MotifRecord:
    return MotifRecord(
        id=_TEST_VEC_PREFIX + suffix,
        symbol='<symbol id="motif-x" overflow="visible"><circle r="0.5"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject="pig",
        scope=scope,
        variant_group="vecgrp",
        embedding=embedding,
    )


def test_upsert_get_roundtrip_and_idempotent():
    store = _store()
    record = MotifRecord(
        id=_TEST_ID_ROUNDTRIP,
        symbol='<symbol id="motif-x" overflow="visible"><circle r="0.5"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject="pig",
        scope="whole",
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
        assert got.scope == "whole"
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
    store = _store()
    vec = [0.1, 0.2, 0.3, -0.5]
    record = MotifRecord(
        id=_TEST_ID_EMBEDDING,
        symbol='<symbol id="motif-x" overflow="visible"><circle r="0.5"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject="pig",
        scope="whole",
        embedding=vec,
    )
    try:
        store.upsert(record)
        got = store.get(_TEST_ID_EMBEDDING)
        assert got is not None
        assert got.embedding == pytest.approx(vec, rel=1e-6)
    finally:
        _delete(store, _TEST_ID_EMBEDDING)


def test_find_best_by_embedding_matches_python_cosine():
    # The DB-side `<=>` ranking must pick the same winner as the old Python cosine (#3).
    # 3-d test vectors keep us isolated from real 1536-d rows: the vector_dims guard in
    # find_best_by_embedding filters them out (and so exercises that guard).
    store = _store()
    scope = "whole"
    query = [1.0, 0.0, 0.0]
    rows = {
        "a": [1.0, 0.0, 0.0],  # cos 1.0  -> winner
        "b": [0.0, 1.0, 0.0],  # cos 0.0
        "c": [0.7, 0.7, 0.0],  # cos ~0.707
    }
    ids = [_TEST_VEC_PREFIX + s for s in rows]
    try:
        for suffix, emb in rows.items():
            store.upsert(_vec_record(suffix, scope, emb))

        match = store.find_best_by_embedding(scope, query)
        assert match is not None
        assert match.id == _TEST_VEC_PREFIX + "a"  # highest python cosine
        assert match.variant_group == "vecgrp"
        # float4 storage => not bit-identical; selection is what must agree exactly.
        assert match.similarity == pytest.approx(_py_cosine(query, rows["a"]), abs=1e-5)
    finally:
        for i in ids:
            _delete(store, i)


def test_find_best_by_embedding_tie_break_lowest_id():
    # Two equal-distance candidates -> deterministic lowest-id winner (ORDER BY ..., id).
    store = _store()
    scope = "whole"
    query = [1.0, 0.0, 0.0]
    ids = [_TEST_VEC_PREFIX + "tie-a", _TEST_VEC_PREFIX + "tie-b"]
    try:
        store.upsert(_vec_record("tie-b", scope, [1.0, 0.0, 0.0]))
        store.upsert(_vec_record("tie-a", scope, [1.0, 0.0, 0.0]))
        match = store.find_best_by_embedding(scope, query)
        assert match is not None and match.id == _TEST_VEC_PREFIX + "tie-a"
    finally:
        for i in ids:
            _delete(store, i)


def test_find_facets_meta_excludes_payload():
    # find_facets_meta returns lightweight rows (no symbol/embedding); locate ours by id
    # without asserting on shared-DB row counts.
    store = _store()
    rec = _vec_record("meta", "whole", [1.0, 0.0, 0.0])
    try:
        store.upsert(rec)
        mine = next(
            (m for m in store.find_facets_meta("whole") if m.id == rec.id), None
        )
        assert mine is not None
        assert mine.subject == "pig" and mine.scope == "whole"
        assert mine.variant_group == "vecgrp"
        assert not hasattr(mine, "symbol") and not hasattr(mine, "embedding")
    finally:
        _delete(store, rec.id)


def test_find_by_variant_group_and_delete_roundtrip():
    # Reusable pool lookup and delete round-trip through Postgres.
    store = _store()
    record = MotifRecord(
        id=_TEST_ID_POOL,
        symbol='<symbol id="motif-x" overflow="visible"><circle r="0.5"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject="pig",
        scope="whole",
        variant_group="abc123",
    )
    try:
        store.upsert(record)
        assert _TEST_ID_POOL in {r.id for r in store.find_by_variant_group("abc123")}

        # delete: the row is gone.
        store.delete(_TEST_ID_POOL)
        assert store.get(_TEST_ID_POOL) is None
    finally:
        _delete(store, _TEST_ID_POOL)
