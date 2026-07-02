"""Session 17 acceptance tests: persistence wiring, restore API, deterministic cost guard.

Complements ``tests/test_sessions.py`` (session 16's edit/gate contract). External
clients are scripted fakes; there is no live Postgres in these tests -- the Postgres
checkpointer path is exercised via monkeypatched ``psycopg``/``checkpointer_from_settings``
seams, not a real database (the acceptance's ``live_db``-marked variant is separate).
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._fakes import _ScriptedRecraft
from tests.test_sessions import (
    BASE_MOTIF,
    BASE_STRIPE,
    _STAR_SVG,
    _committed_intent,
    _set_author,
    _set_edit,
    fake_preview,
)

client = TestClient(app)


# --- acceptance #1: restore -> byte-identical recompose -----------------------


def test_restore_recompose_is_byte_identical(fake_preview):
    import app.sessions.graph as sg
    from app.engine.candidates import SOURCE_FIDELITY_VECTOR, generate_candidate_set

    _set_author(BASE_STRIPE)
    sid = "restore-byte"
    result = sg.run_turn(sid, prompt="stripes")
    before_svg = result["render_batch"][0]["svg"]

    restored = sg.get_state(sid)
    recompose = generate_candidate_set(
        [restored["current_intent"]],
        seed=restored["seed"],
        colorway=restored.get("colorway"),
        source_fidelity=SOURCE_FIDELITY_VECTOR,
        registry_version=restored["registry_version"],
    )
    assert recompose.candidates[0].candidate.svg == before_svg

    # The GET surface backing this recompose deliberately hides `current_intent` (§16).
    resp = client.get(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 200
    assert "current_intent" not in resp.json()


# --- acceptance #2: survives a process restart ---------------------------------


def test_session_survives_checkpointer_rebuild(fake_preview, monkeypatch):
    import app.sessions.graph as sg
    from langgraph.checkpoint.memory import MemorySaver

    # A shared MemorySaver stands in for a durable (Postgres) backend: rebuilding the
    # graph must not lose state as long as the checkpointer itself survives.
    shared_saver = MemorySaver()
    monkeypatch.setattr(sg, "checkpointer_from_settings", lambda settings: shared_saver)

    _set_author(BASE_STRIPE)
    sid = "restart-1"
    resp = client.post("/api/v1/generate", json={"session_id": sid, "prompt": "stripes"})
    assert resp.status_code == 200
    before = _committed_intent(sid)

    sg.reset_sessions()  # simulate a process restart: graph rebuilt, same durable saver

    restore = client.get(f"/api/v1/sessions/{sid}")
    assert restore.status_code == 200
    assert restore.json()["seed"] == before["seed"]

    _set_edit([{"name": "set_stripe", "args": {"layer_id": "stripe_base", "angle": 45}}])
    resp2 = client.post(
        "/api/v1/generate", json={"session_id": sid, "prompt": "make it 45 degrees"}
    )
    assert resp2.status_code == 200
    after = _committed_intent(sid)
    stripe_angle = next(l for l in after["layers"] if l["type"] == "stripe")["params"]["angle"]
    assert stripe_angle == 45  # the edit applied against the RESTORED intent, not a blank one


# --- GET /sessions/{id} shape ---------------------------------------------------


def test_get_unknown_session_is_404():
    resp = client.get("/api/v1/sessions/does-not-exist")
    assert resp.status_code == 404


def test_get_restores_conversation_and_hides_intent(fake_preview):
    _set_author(BASE_STRIPE)
    sid = "restore-shape"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "stripes"})
    resp = client.get(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert body["pending"] is None
    assert body["seed"] == 7  # BASE_STRIPE's seed
    assert len(body["turns"]) >= 1
    assert len(body["candidates"]) == 1
    assert set(body["candidates"][0]) <= {"id", "png_url", "colorway_id"}
    assert "current_intent" not in body


def test_get_restores_pending_gate_with_budget_hint(fake_preview):
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "restore-pending"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars"})
    resp = client.get(f"/api/v1/sessions/{sid}")
    assert resp.status_code == 200
    pending = resp.json()["pending"]
    assert pending["type"] == "motif_candidates"
    assert pending["budget"] == {"recraft_used": 0, "recraft_limit": 3}  # default limit


# --- acceptance #3: deterministic cost guard (recraft) -------------------------


def test_recraft_budget_blocks_after_limit(fake_preview, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SESSION_RECRAFT_LIMIT", "1")
    get_settings.cache_clear()
    rec = _ScriptedRecraft(_STAR_SVG, _STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "budget-1"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars"})
    first = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert first.status_code == 200, first.text
    assert len(rec.calls) == 1

    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a moon", "subject": "moon", "scope": "whole"}}]
    )
    turn2 = client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use a moon instead"})
    assert turn2.status_code == 200
    assert turn2.json()["pending"]["budget"] == {"recraft_used": 1, "recraft_limit": 1}

    blocked = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert blocked.status_code == 429
    assert len(rec.calls) == 1  # never fired a second time

    from app.sessions.graph import awaiting_gate

    assert awaiting_gate(sid) is True  # rejection does not wedge the gate


def test_recraft_budget_zero_blocks_before_first_call(fake_preview, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SESSION_RECRAFT_LIMIT", "0")
    get_settings.cache_clear()
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "budget-0"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars"})
    resp = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert resp.status_code == 429
    assert rec.calls == []


def test_finalize_budget_and_increment(fake_preview, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SESSION_FINALIZE_LIMIT", "1")
    get_settings.cache_clear()
    import app.api.routes.finalize as fin

    monkeypatch.setattr(fin, "render_fabric", lambda intent, **kwargs: b"PNGDATA")
    _set_author(BASE_MOTIF)
    sid = "finalize-budget"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})

    ok = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "finalize"})
    assert ok.status_code == 200

    from app.sessions.graph import get_state

    assert get_state(sid)["budget"]["finalize_used"] == 1

    blocked = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "finalize"})
    assert blocked.status_code == 429


# --- acceptance #3b: in-flight dedup -------------------------------------------


def test_dedup_blocks_concurrent_confirm(fake_preview):
    from app.sessions.budget import session_inflight

    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "dedup-1"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars"})

    with session_inflight(sid):
        blocked = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert blocked.status_code == 409
    assert rec.calls == []

    ok = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert ok.status_code == 200
    assert len(rec.calls) == 1


# --- acceptance #5: in-memory degrade (never touches psycopg) ------------------


def test_full_session_flow_never_touches_psycopg_without_dsn(fake_preview, monkeypatch):
    import psycopg

    def _boom(*args, **kwargs):
        raise AssertionError("psycopg.connect must not be called without SUPABASE_DB_URL")

    monkeypatch.setattr(psycopg, "connect", _boom)
    _set_author(BASE_MOTIF)
    sid = "degrade-full"
    resp = client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    assert resp.status_code == 200
    restore = client.get(f"/api/v1/sessions/{sid}")
    assert restore.status_code == 200


# --- acceptance #6: no setup()/DDL regression guard ----------------------------


def test_no_ddl_or_schema_setup_calls_in_app():
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    ddl_re = re.compile(r"\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|SCHEMA)\b", re.IGNORECASE)
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if ".setup(" in text:
            offenders.append(f"{path}: .setup(")
        if ddl_re.search(text):
            offenders.append(f"{path}: DDL statement")
        if "db push" in text:
            offenders.append(f"{path}: db push")
    assert not offenders, offenders


# --- acceptance #7: missing tables / stale schema -> clean fail, no self-provision


def test_missing_checkpoint_tables_clean_fail(fake_preview, monkeypatch):
    import psycopg
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake/db")
    get_settings.cache_clear()

    executed: list[str] = []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, *params):
            executed.append(sql)
            if "LIMIT 0" in sql:
                raise psycopg.errors.UndefinedTable('relation "checkpoints" does not exist')
            raise AssertionError(f"unexpected query beyond the table probe: {sql}")

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn())

    _set_author(BASE_STRIPE)
    resp = client.post("/api/v1/generate", json={"session_id": "missing-tables", "prompt": "x"})
    assert resp.status_code == 502
    assert "monorepo" in resp.text.lower()
    assert not any(re.search(r"\b(CREATE|ALTER|DROP)\b", q, re.IGNORECASE) for q in executed)


def test_checkpoint_migrations_version_mismatch_clean_fail(fake_preview, monkeypatch):
    from app.core.config import get_settings
    import psycopg

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake/db")
    get_settings.cache_clear()

    executed: list[str] = []

    class _Result:
        def fetchone(self):
            return (3,)  # behind the pinned langgraph-checkpoint-postgres==3.1.0 (v9)

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, *params):
            executed.append(sql)
            return None if "LIMIT 0" in sql else _Result()

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn())

    _set_author(BASE_STRIPE)
    resp = client.post("/api/v1/generate", json={"session_id": "old-version", "prompt": "x"})
    assert resp.status_code == 502
    assert "3.1.0" in resp.text
    assert not any(re.search(r"\b(CREATE|ALTER|DROP)\b", q, re.IGNORECASE) for q in executed)
