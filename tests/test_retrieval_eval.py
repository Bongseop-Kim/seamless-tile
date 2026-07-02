"""S19 offline calibration guardrails + live-Supabase pgvector index parity for the
motif retrieval cascade.

The offline tests pin the shipped `motif_similarity_tau` default against the observed
sweep on tests/fixtures/motif_eval/labelset.json (scripts/eval_motif_retrieval.py) --
if the labelset, the cached embeddings, or the config default drift out of sync, one
of these fails loudly instead of the mismatch going unnoticed (spec §12). Re-run
`scripts/eval_motif_retrieval.py --embed` and recalibrate together with the config
default when EMBEDDING_MODEL or the labelset changes.

The live_db test is opt-in only (see tests/conftest.py / RUN_LIVE_SUPABASE_TESTS) --
it never runs in default CI/local pytest, matching tests/test_motif_store_pg.py. It
reads the live table as-is (no seeding/cleanup): a full Python cosine scan must agree
with pgvector's `<=>` ranking, which would catch a future ANN index (HNSW/IVFFlat)
returning an approximate top-1 instead of the exact nearest neighbor.
"""

import os

import pytest

from app.adapters.motif_resolver import _descriptor_text
from app.core.config import Settings
from app.motifs.facets import normalize_facet
from scripts.eval_motif_retrieval import (
    load_embeddings,
    load_labelset,
    metrics,
    missing_texts,
    predict,
    score_queries,
)


# --- offline: fixture/cache/config consistency -------------------------------


def test_fixture_embeddings_complete():
    # Guards against editing labelset.json / forgetting --embed: every corpus row and
    # query descriptor must have a cached vector, sharing one dimension, pinned to the
    # *shipped* default model (Settings(_env_file=None), not get_settings(), so a
    # .env override can't make this pass by accident).
    labelset = load_labelset()
    cache = load_embeddings()
    assert missing_texts(labelset, cache) == []
    assert {len(v) for v in cache["vectors"].values()} == {1536}
    assert cache["model"] == Settings(_env_file=None).embedding_model


def test_default_tau_meets_baseline():
    # Pins the shipped motif_similarity_tau (app/core/config.py) against the S19
    # sweep: zero false reuse is a hard requirement; recall keeps slack below the
    # observed 0.5625 so minor wobble doesn't flake, but a collapse back toward
    # reuse-everything still fails loud.
    labelset = load_labelset()
    cache = load_embeddings()
    scored = score_queries(labelset, cache["vectors"])
    tau = Settings(_env_file=None).motif_similarity_tau
    m = metrics(scored, tau)
    assert m["false_reuse_rate"] == 0.0
    assert m["precision"] == 1.0
    assert m["recall"] >= 0.5


def test_cross_scope_hard_filter():
    # cross-scope queries put a corpus subject at the WRONG scope (e.g. strawberry is
    # whole-only, queried as partial). The hard scope filter must (a) never let a
    # same-subject different-scope row surface as best_id, and (b) at the shipped tau
    # every one of these resolves to generate (max observed sim 0.648 < 0.84).
    labelset = load_labelset()
    cache = load_embeddings()
    scored = score_queries(labelset, cache["vectors"])
    corpus_scope = {row["id"]: row["scope"] for row in labelset["corpus"]}
    tau = Settings(_env_file=None).motif_similarity_tau

    cross_scope = [r for r in scored if r["category"] == "cross-scope"]
    assert cross_scope  # sanity: the fixture must actually carry this category
    for row in cross_scope:
        query = next(q for q in labelset["queries"] if q["name"] == row["name"])
        want_scope = normalize_facet(query["spec"].get("scope"))
        if row["best_id"] is not None:
            assert corpus_scope[row["best_id"]] == want_scope
        assert predict(row, tau) == "generate"

    strawberry_row = next(r for r in cross_scope if r["name"] == "cross-strawberry-partial")
    assert strawberry_row["best_id"] != "corpus-strawberry"  # same-subject/wrong-scope excluded


# --- live_db: pgvector index vs full-scan parity ------------------------------


def _live_supabase_enabled() -> bool:
    dsn = os.environ.get("SUPABASE_DB_URL")
    enabled = os.environ.get("RUN_LIVE_SUPABASE_TESTS") == "1"
    return enabled and bool(dsn and dsn.strip())


def _py_cosine(a: list[float], b: list[float]) -> float:
    import math

    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


@pytest.mark.live_db
@pytest.mark.skipif(
    not _live_supabase_enabled(),
    reason="set RUN_LIVE_SUPABASE_TESTS=1 and SUPABASE_DB_URL for live Supabase tests",
)
def test_live_index_matches_seq_scan_baseline():
    from app.core.config import get_settings
    from app.motifs.store import PostgresMotifStore

    store = PostgresMotifStore(get_settings().supabase_db_url)
    labelset = load_labelset()
    vectors = load_embeddings()["vectors"]
    rows = store.all()  # read-only: no seeding, no cleanup

    for query in labelset["queries"]:
        scope = normalize_facet(query["spec"].get("scope"))
        vec = vectors.get(_descriptor_text(query["spec"]))
        if vec is None:
            continue

        db_match = store.find_best_by_embedding(scope, vec)

        best = None  # (record, sim)
        for rec in sorted((r for r in rows if r.scope == scope), key=lambda r: r.id):
            emb = rec.embedding
            if not emb or len(emb) != len(vec):
                continue
            sim = _py_cosine(vec, emb)
            if best is None or sim > best[1]:
                best = (rec, sim)

        if best is None:
            assert db_match is None
            continue
        assert db_match is not None
        assert db_match.id == best[0].id
        assert db_match.similarity == pytest.approx(best[1], abs=1e-4)
