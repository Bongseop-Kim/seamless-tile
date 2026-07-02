"""Session 18 acceptance tests: fork/undo/redo (time-travel) over the session graph's
checkpoints (spec §14/S14). Undo/redo is a read-only restore via ``checkpoint_id``; fork
is a new turn run ``from_checkpoint``. External clients are scripted fakes, per
``tests/test_sessions.py`` / ``tests/test_session_persistence.py``.
"""

from __future__ import annotations

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
    _stripe_angle,
    fake_preview,
)

client = TestClient(app)


# --- AC1: rewind (undo) -> byte-identical recompose ----------------------------


def test_rewind_recompose_byte_identical(fake_preview):
    import app.sessions.graph as sg
    from app.engine.candidates import SOURCE_FIDELITY_VECTOR, generate_candidate_set

    _set_author(BASE_STRIPE)
    sid = "tt-rewind"
    turn1 = sg.run_turn(sid, prompt="stripes")
    turn1_svg = turn1["render_batch"][0]["svg"]

    _set_edit([{"name": "set_stripe", "args": {"layer_id": "stripe_base", "angle": 45}}])
    sg.run_turn(sid, prompt="make it 45 degrees")

    resp = client.get(f"/api/v1/sessions/{sid}/checkpoints")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    checkpoints = body["checkpoints"]
    assert len(checkpoints) == 2  # one per committed turn, oldest first
    for cp in checkpoints:
        assert set(cp) == {"checkpoint_id", "created_at", "turns", "prompt"}
    assert checkpoints[0]["turns"] < checkpoints[1]["turns"]
    assert checkpoints[0]["prompt"] == "stripes"
    assert checkpoints[1]["prompt"] == "make it 45 degrees"
    cp1_id = checkpoints[0]["checkpoint_id"]

    # rewind: read-only restore of turn 1's committed intent
    restored = sg.get_state(sid, cp1_id)
    recompose = generate_candidate_set(
        [restored["current_intent"]],
        seed=restored["seed"],
        colorway=restored.get("colorway"),
        source_fidelity=SOURCE_FIDELITY_VECTOR,
        registry_version=restored["registry_version"],
    )
    assert recompose.candidates[0].candidate.svg == turn1_svg

    # GET with checkpoint_id: read-only view, no pending, current_intent still hidden
    getresp = client.get(f"/api/v1/sessions/{sid}", params={"checkpoint_id": cp1_id})
    assert getresp.status_code == 200
    gbody = getresp.json()
    assert gbody["pending"] is None
    assert "current_intent" not in gbody

    # an unknown checkpoint_id degrades to the existing empty-values -> 404 path
    missing = client.get(f"/api/v1/sessions/{sid}", params={"checkpoint_id": "not-a-real-id"})
    assert missing.status_code == 404


# --- AC2: fork preserves the original branch -----------------------------------


def test_fork_preserves_original_branch(fake_preview):
    import app.sessions.graph as sg
    from app.engine.candidates import SOURCE_FIDELITY_VECTOR, generate_candidate_set

    _set_author(BASE_STRIPE)
    sid = "tt-fork"
    sg.run_turn(sid, prompt="stripes")  # turn 1: angle 30 (BASE_STRIPE)

    _set_edit([{"name": "set_stripe", "args": {"layer_id": "stripe_base", "angle": 45}}])
    turn2 = sg.run_turn(sid, prompt="make it 45 degrees")
    turn2_svg = turn2["render_batch"][0]["svg"]

    checkpoints = sg.list_turn_checkpoints(sid)
    assert len(checkpoints) == 2
    cp1_id, cp2_id = (c["checkpoint_id"] for c in checkpoints)

    # fork from cp1 (before the 45-degree edit) with a DIFFERENT edit
    _set_edit([{"name": "set_stripe", "args": {"layer_id": "stripe_base", "angle": 60}}])
    resp = client.post(
        "/api/v1/generate",
        json={"session_id": sid, "prompt": "make it 60 degrees", "from_checkpoint": cp1_id},
    )
    assert resp.status_code == 200, resp.text
    assert _stripe_angle(_committed_intent(sid)) == 60  # new head = the fork branch

    checkpoints_after = sg.list_turn_checkpoints(sid)
    assert len(checkpoints_after) == 3  # turn1 + original turn2 + the fork turn

    # the ORIGINAL branch (cp2, the 45-degree commit) is untouched, still byte-identical
    restored_cp2 = sg.get_state(sid, cp2_id)
    recompose = generate_candidate_set(
        [restored_cp2["current_intent"]],
        seed=restored_cp2["seed"],
        colorway=restored_cp2.get("colorway"),
        source_fidelity=SOURCE_FIDELITY_VECTOR,
        registry_version=restored_cp2["registry_version"],
    )
    assert recompose.candidates[0].candidate.svg == turn2_svg


# --- AC3: redo restores the next checkpoint ------------------------------------


def test_redo_restores_next_checkpoint(fake_preview):
    import app.sessions.graph as sg
    from app.engine.candidates import SOURCE_FIDELITY_VECTOR, generate_candidate_set

    _set_author(BASE_STRIPE)
    sid = "tt-redo"
    sg.run_turn(sid, prompt="stripes")
    _set_edit([{"name": "set_stripe", "args": {"layer_id": "stripe_base", "angle": 45}}])
    turn2 = sg.run_turn(sid, prompt="make it 45 degrees")
    turn2_svg = turn2["render_batch"][0]["svg"]

    checkpoints = sg.list_turn_checkpoints(sid)
    cp1_id, cp2_id = (c["checkpoint_id"] for c in checkpoints)

    undo = client.get(f"/api/v1/sessions/{sid}", params={"checkpoint_id": cp1_id})
    assert undo.status_code == 200

    redo = client.get(f"/api/v1/sessions/{sid}", params={"checkpoint_id": cp2_id})
    assert redo.status_code == 200

    restored = sg.get_state(sid, cp2_id)
    recompose = generate_candidate_set(
        [restored["current_intent"]],
        seed=restored["seed"],
        colorway=restored.get("colorway"),
        source_fidelity=SOURCE_FIDELITY_VECTOR,
        registry_version=restored["registry_version"],
    )
    assert recompose.candidates[0].candidate.svg == turn2_svg


# --- budget guard: fork must not refund spend already made on the head --------


def test_fork_does_not_refund_recraft_budget(fake_preview, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SESSION_RECRAFT_LIMIT", "1")
    get_settings.cache_clear()
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "tt-budget"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars"})

    import app.sessions.graph as sg

    checkpoints = sg.list_turn_checkpoints(sid)
    assert len(checkpoints) == 1  # turn 2 is still paused at the gate, not committed yet
    cp1_id = checkpoints[0]["checkpoint_id"]

    confirmed = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert confirmed.status_code == 200, confirmed.text
    assert len(rec.calls) == 1
    assert sg.get_state(sid)["budget"]["recraft_used"] == 1

    # fork from BEFORE the spent Recraft call, with a different edit that also gates
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a moon", "subject": "moon", "scope": "whole"}}]
    )
    forked = client.post(
        "/api/v1/generate",
        json={"session_id": sid, "prompt": "use a moon instead", "from_checkpoint": cp1_id},
    )
    assert forked.status_code == 200, forked.text
    # the carried-forward budget already shows the head's spend, not the fork point's
    assert forked.json()["pending"]["budget"] == {"recraft_used": 1, "recraft_limit": 1}

    blocked = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert blocked.status_code == 429
    assert len(rec.calls) == 1  # the fork never got a second real Recraft call


# --- API guard ------------------------------------------------------------------


def test_from_checkpoint_without_session_is_422():
    resp = client.post("/api/v1/generate", json={"prompt": "x", "from_checkpoint": "some-id"})
    assert resp.status_code == 422
    assert "from_checkpoint" in str(resp.json()["detail"])


def test_fork_unknown_checkpoint_is_404(fake_preview):
    import app.sessions.graph as sg

    _set_author(BASE_STRIPE)
    sid = "tt-fork-bogus"
    sg.run_turn(sid, prompt="stripes")
    before = sg.list_turn_checkpoints(sid)

    resp = client.post(
        "/api/v1/generate",
        json={"session_id": sid, "prompt": "again", "from_checkpoint": "not-a-real-id"},
    )
    assert resp.status_code == 404
    assert "not-a-real-id" in str(resp.json()["detail"])
    # no silent head move: the bogus fork must not have authored a new turn
    assert sg.list_turn_checkpoints(sid) == before


def test_fork_cross_session_checkpoint_is_404(fake_preview):
    import app.sessions.graph as sg

    _set_author(BASE_STRIPE)
    sg.run_turn("tt-cross-a", prompt="stripes")
    cp_a = sg.list_turn_checkpoints("tt-cross-a")[0]["checkpoint_id"]

    sg.run_turn("tt-cross-b", prompt="stripes")
    before = sg.list_turn_checkpoints("tt-cross-b")

    resp = client.post(
        "/api/v1/generate",
        json={"session_id": "tt-cross-b", "prompt": "again", "from_checkpoint": cp_a},
    )
    assert resp.status_code == 404
    assert sg.list_turn_checkpoints("tt-cross-b") == before
