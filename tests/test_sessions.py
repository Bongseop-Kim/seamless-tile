"""Session 16 acceptance tests: edit-as-delta + confirm gate (spec §14).

Sealing tests for the P0 contract: edit locality, whitelist enforcement, the cost gate
(Recraft never fires without an explicit confirm), apply_tools determinism, stateless
compatibility, and in-memory degrade. External clients are scripted fakes; the module
TestClient does not run lifespan, so no real keys/clients are installed.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests._fakes import _ScriptedEditLLM, _ScriptedLLM, _ScriptedRecraft
from tests.test_intent import mvp_intent  # registers the circle/bee test motifs on import

client = TestClient(app)

# A diagonal-stripe design. The prompt path normalizes any diagonal stripe to -45° with a
# tile-commensurate period, so turn 1 commits a stripe at -45°; a later set_stripe(45) then
# flips only the angle (same slope class → period stays commensurate, no repair).
BASE_STRIPE = {
    "intent_version": 1,
    "canvas": {"tile_mm": 48, "dpi": 300},
    "seed": 7,
    "production": {"method": "print", "max_colors": 12},
    "palette": {"slots": [{"id": "ground", "hex": "#10243a"}, {"id": "accent", "hex": "#ef8a7a"}]},
    "colorways": [
        {"id": "default", "name": "default", "mapping": {"ground": "#10243a", "accent": "#ef8a7a"}}
    ],
    "layers": [
        {"id": "bg", "type": "background", "z_order": 0, "params": {"color": "ground"}},
        {
            "id": "stripe_base",
            "type": "stripe",
            "z_order": 1,
            "params": {
                "angle": 30,
                "period_mm": 9.6,
                "bands": [{"offset_mm": 0, "width_mm": 4.8, "color": "accent"}],
            },
        },
    ],
}

# A single motif (registered "circle") placed at one point — no stripe, so no normalization.
BASE_MOTIF = {
    "intent_version": 1,
    "canvas": {"tile_mm": 48, "dpi": 300},
    "seed": 5,
    "production": {"method": "print", "max_colors": 12},
    "palette": {"slots": [{"id": "ground", "hex": "#10243a"}, {"id": "ink", "hex": "#f5ca57"}]},
    "colorways": [
        {"id": "default", "name": "default", "mapping": {"ground": "#10243a", "ink": "#f5ca57"}}
    ],
    "layers": [
        {"id": "bg", "type": "background", "z_order": 0, "params": {"color": "ground"}},
        {
            "id": "dots",
            "type": "motif",
            "z_order": 1,
            "params": {"motif_id": "circle", "size_mm": 4, "color": "ink"},
            "placement": {"type": "point_set", "point_set": {"points": [[24, 24]]}},
        },
    ],
}

_STAR_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<path d="M5 0 L6 4 L10 4 L7 6 L8 10 L5 8 L2 10 L3 6 L0 4 L4 4 Z" fill="#f5ca57"/></svg>'
)


def _author_json(intent: dict) -> str:
    return json.dumps({"designs": [{"intent": intent, "motif_specs": []}]})


def _set_author(intent: dict) -> None:
    from app.adapters.llm import set_default_client

    set_default_client(_ScriptedLLM(_author_json(intent)))


def _set_edit(*tool_call_lists: list[dict]) -> None:
    from app.adapters.edit_llm import set_default_edit_client

    set_default_edit_client(_ScriptedEditLLM(*tool_call_lists))


@pytest.fixture
def fake_preview(monkeypatch):
    import app.api.routes.generate as route

    monkeypatch.setattr(route, "preview_configured", lambda: True)
    monkeypatch.setattr(
        route, "make_preview", lambda svg, *, tile_mm, dpi, path: f"https://preview.test/{path}"
    )


def _committed_intent(session_id: str) -> dict:
    from app.sessions.graph import get_state

    return copy.deepcopy(get_state(session_id)["current_intent"])


def _stripe_angle(intent: dict) -> float:
    return next(layer for layer in intent["layers"] if layer["type"] == "stripe")[
        "params"
    ]["angle"]


class _CandidateStore:
    def __init__(self, *records) -> None:
        self.rows = {r.id: r for r in records}

    def upsert(self, record) -> None:
        self.rows.setdefault(record.id, record)

    def get(self, motif_id: str):
        return self.rows.get(motif_id)

    def all(self):
        return sorted(self.rows.values(), key=lambda r: r.id)

    def all_ids(self):
        return sorted(self.rows)

    def find_facets_meta(self, scope):
        from app.motifs.store import MotifMeta

        return [
            MotifMeta(
                id=r.id,
                variant_group=r.variant_group,
                subject=r.subject,
                scope=r.scope,
                view=r.view,
                expression=r.expression,
                style=r.style,
                description=r.description,
            )
            for r in self.all()
            if r.scope == scope
        ]

    def find_best_by_embedding(self, scope, query_vec):
        return None

    def find_by_variant_group(self, variant_group):
        return [
            r for r in self.all() if r.variant_group == variant_group
        ]

    def delete(self, motif_id):
        self.rows.pop(motif_id, None)


def _offer_motif(motif_id: str, *, subject: str, scope: str) -> None:
    from app.motifs.registry import get_motif
    from app.motifs.store import MotifRecord, set_default_store

    motif = get_motif(motif_id)
    set_default_store(
        _CandidateStore(
            MotifRecord(
                id=motif.id,
                symbol=motif.symbol,
                bbox_mm=motif.bbox_mm,
                anchor=motif.anchor,
                subject=subject,
                scope=scope,
                source="builtin",
                color_slots=list(motif.color_slots),
            )
        )
    )


# --- acceptance #6: stateless compatibility -----------------------------------


def test_stateless_response_shape_unchanged(fake_preview):
    resp = client.post("/api/v1/generate", json={"intent": mvp_intent(), "candidate_count": 2})
    assert resp.status_code == 200
    # exclude_none drops the null session fields → identical to the pre-session shape.
    assert set(resp.json()) == {"request_id", "candidates", "warnings"}


# --- acceptance #1: edit locality ---------------------------------------------


def test_edit_locality_stripe_angle(fake_preview):
    _set_author(BASE_STRIPE)
    _set_edit([{"name": "set_stripe", "args": {"layer_id": "stripe_base", "angle": 45}}])
    sid = "loc-stripe"
    assert client.post("/api/v1/generate", json={"session_id": sid, "prompt": "diagonal"}).status_code == 200
    before = _committed_intent(sid)
    assert client.post(
        "/api/v1/generate", json={"session_id": sid, "prompt": "make the stripe 45 degrees"}
    ).status_code == 200
    after = _committed_intent(sid)

    def blank_stripe_angle(i: dict) -> dict:
        j = copy.deepcopy(i)
        for la in j["layers"]:
            if la["type"] == "stripe":
                la["params"]["angle"] = None
        return j

    assert blank_stripe_angle(before) == blank_stripe_angle(after)  # everything else identical
    assert _stripe_angle(before) != _stripe_angle(after)
    assert _stripe_angle(after) == 45


def test_edit_locality_palette_slot(fake_preview):
    _set_author(BASE_STRIPE)
    _set_edit([{"name": "set_palette_slot", "args": {"slot_id": "accent", "hex": "#3366cc"}}])
    sid = "loc-palette"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "diagonal"})
    before = _committed_intent(sid)
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "make the accent blue"})
    after = _committed_intent(sid)

    slot_hex = {s["id"]: s["hex"] for s in after["palette"]["slots"]}
    assert slot_hex["accent"] == "#3366cc"
    assert slot_hex["ground"] == "#10243a"  # untouched
    assert after["layers"] == before["layers"]  # geometry untouched
    assert after["colorways"][0]["mapping"]["accent"] == "#3366cc"  # colorway follows


# --- acceptance #2: whitelist enforcement -------------------------------------


def test_whitelist_and_bad_args_do_not_apply():
    from app.sessions.tools import apply_tools

    baseline = apply_tools(BASE_STRIPE, []).intent
    out = apply_tools(
        BASE_STRIPE,
        [
            {"name": "frobnicate", "args": {"anything": 1}},  # not in whitelist
            {"name": "set_palette_slot", "args": {"slot_id": "nope", "hex": "#123456"}},  # bad id
        ],
    )
    assert out.intent == baseline  # nothing applied
    assert any("frobnicate" in w for w in out.warnings)
    assert any("nope" in w for w in out.warnings)


def test_bad_hex_is_rejected():
    from app.sessions.tools import apply_tools

    out = apply_tools(
        BASE_STRIPE, [{"name": "set_palette_slot", "args": {"slot_id": "accent", "hex": "blue"}}]
    )
    assert out.intent == apply_tools(BASE_STRIPE, []).intent
    assert any("#RRGGBB" in w for w in out.warnings)


def test_tool_parse_errors_are_skipped_with_warnings():
    from app.sessions.tools import apply_tools

    motif_out = apply_tools(
        BASE_MOTIF,
        [
            {"name": "scale_motif", "args": {"layer_id": "dots", "factor": "big"}},
            {"name": "set_density", "args": {"layer_id": "dots", "spacing_mm": "dense"}},
        ],
    )
    stripe_out = apply_tools(
        BASE_STRIPE,
        [
            {"name": "set_stripe", "args": {"layer_id": "stripe_base", "angle": "steep"}},
            {"name": "set_stripe", "args": {"layer_id": "stripe_base", "period_mm": "wide"}},
            {"name": "add_layer", "args": {"layer": {"id": "bad", "type": "mystery"}}},
        ],
    )
    assert motif_out.intent == apply_tools(BASE_MOTIF, []).intent
    assert stripe_out.intent == apply_tools(BASE_STRIPE, []).intent
    warnings = motif_out.warnings + stripe_out.warnings
    assert any("factor must be a number" in w for w in warnings)
    assert any("spacing_mm must be a number" in w for w in warnings)
    assert any("angle must be a number" in w for w in warnings)
    assert any("period_mm must be a number" in w for w in warnings)
    assert any("add_layer.layer is invalid" in w for w in warnings)


# --- acceptance #4: apply_tools determinism -----------------------------------


def test_apply_tools_is_deterministic():
    from app.sessions.tools import apply_tools

    calls = [
        {"name": "set_stripe", "args": {"layer_id": "stripe_base", "angle": 12}},
        {"name": "set_palette_slot", "args": {"slot_id": "accent", "hex": "#abcdef"}},
    ]
    a = apply_tools(BASE_STRIPE, calls)
    b = apply_tools(BASE_STRIPE, calls)
    assert a.intent == b.intent
    assert _stripe_angle(a.intent) == 12


def test_set_material_writes_state_not_intent():
    from app.sessions.tools import apply_tools

    out = apply_tools(
        BASE_MOTIF, [{"name": "set_material", "args": {"target": "dots", "fabric": "linen"}}]
    )
    assert out.intent == apply_tools(BASE_MOTIF, []).intent  # engine intent untouched (§7)
    assert out.state_updates["material_map"] == {"dots": {"fabric": "linen"}}


# --- acceptance #3: cost gate (the core seal) ---------------------------------


def test_reuse_candidate_presentation_is_free(fake_preview):
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "gate-free"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    resp = client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars instead"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending"] is not None
    assert body["pending"]["type"] == "motif_candidates"
    assert body["turn_id"]
    assert rec.calls == []  # presenting reuse candidates never calls Recraft


def test_recraft_fires_only_after_explicit_confirm(fake_preview):
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "gate-confirm"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars instead"})
    assert rec.calls == []  # still free while awaiting the decision

    client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert len(rec.calls) == 1  # Recraft fired exactly once, only on the explicit confirm


def test_confirm_generate_rejected_when_not_awaiting(fake_preview):
    _set_author(BASE_MOTIF)
    sid = "gate-none"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    # No pending gate → confirming a generation is a 409, not a silent Recraft call.
    resp = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert resp.status_code == 409


def test_select_existing_motif_is_free_and_commits(fake_preview):
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _offer_motif("bee", subject="bee", scope="whole")
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a bee", "subject": "bee", "scope": "whole"}}]
    )
    sid = "gate-select"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use a bee"})
    # Pick the registered "bee" motif — free, no Recraft.
    resp = client.post(
        f"/api/v1/sessions/{sid}/select-motif", json={"layer_id": "dots", "motif_id": "bee"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["turn_id"]
    assert rec.calls == []
    intent = _committed_intent(sid)
    assert (
        next(layer for layer in intent["layers"] if layer["id"] == "dots")["params"][
            "motif_id"
        ]
        == "bee"
    )


# --- acceptance #3c: finalize gate --------------------------------------------


def test_finalize_only_on_explicit_confirm(fake_preview, monkeypatch):
    import app.api.routes.finalize as fin

    calls = {"n": 0}

    def spy(intent, **kwargs):
        calls["n"] += 1
        return b"PNGDATA"

    monkeypatch.setattr(fin, "render_fabric", spy)
    _set_author(BASE_MOTIF)
    sid = "fin"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    assert calls["n"] == 0  # committing a candidate does not finalize

    resp = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "finalize"})
    assert resp.status_code == 200
    assert calls["n"] == 1  # fabric render ran only on the explicit finalize button


# --- set_material flows into the finalize render (§7 -> fabric) ----------------


def _finalize_with_session_material(sid, monkeypatch, confirm_body):
    """Author a yarn_dyed stripe, set_material on the `accent` slot, then finalize.
    Returns the kwargs the fabric render was called with."""
    import app.api.routes.finalize as fin

    captured: dict = {}

    def spy(intent, **kwargs):
        captured.update(kwargs)
        return b"PNGDATA"

    monkeypatch.setattr(fin, "render_fabric", spy)
    yarn = copy.deepcopy(BASE_STRIPE)
    yarn["production"]["method"] = "yarn_dyed"
    _set_author(yarn)
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "stripes"})
    _set_edit(
        [{"name": "set_material", "args": {"target": "accent", "fabric": "herringbone"}}]
    )
    client.post(
        "/api/v1/generate", json={"session_id": sid, "prompt": "accent in herringbone"}
    )
    resp = client.post(f"/api/v1/sessions/{sid}/confirm", json=confirm_body)
    assert resp.status_code == 200, resp.text
    return captured


def test_set_material_flows_into_finalize(fake_preview, monkeypatch):
    captured = _finalize_with_session_material(
        "fin-material", monkeypatch, {"action": "finalize"}
    )
    assert captured["material_map"] == {"accent": "herringbone"}


def test_finalize_request_material_map_wins_over_session(fake_preview, monkeypatch):
    captured = _finalize_with_session_material(
        "fin-material-override",
        monkeypatch,
        {"action": "finalize", "material_map": {"accent": "pindot"}},
    )
    assert captured["material_map"] == {"accent": "pindot"}


# --- acceptance #7: in-memory degrade -----------------------------------------


def test_session_runs_in_memory_without_db(fake_preview):
    from app.motifs.store import get_default_store

    assert get_default_store() is None  # conftest unsets SUPABASE_DB_URL
    _set_author(BASE_STRIPE)
    sid = "mem"
    resp = client.post("/api/v1/generate", json={"session_id": sid, "prompt": "stripes"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == sid
    assert _committed_intent(sid) is not None


# --- regressions found by the adversarial review ------------------------------

BASE_TEXT = {
    "intent_version": 1,
    "canvas": {"tile_mm": 48, "dpi": 300},
    "seed": 3,
    "production": {"method": "print", "max_colors": 12},
    "palette": {"slots": [{"id": "ground", "hex": "#10243a"}, {"id": "ink", "hex": "#f5ca57"}]},
    "colorways": [
        {"id": "default", "name": "default", "mapping": {"ground": "#10243a", "ink": "#f5ca57"}}
    ],
    "layers": [
        {"id": "bg", "type": "background", "z_order": 0, "params": {"color": "ground"}},
        {
            "id": "word",
            "type": "motif",
            "z_order": 1,
            "params": {"motif_id": "placeholder", "size_mm": 10, "color": "ink"},
            "placement": {"type": "point_set", "point_set": {"points": [[24, 24]]}},
        },
    ],
}


class _FlakyRecraft:
    """Raises on the first `fail_times` calls, then returns `svg` (models a transient
    outage that heals). Records every call."""

    def __init__(self, fail_times: int, svg: str) -> None:
        from app.adapters.base import AdapterClientError

        self._err = AdapterClientError
        self._fail_times = fail_times
        self._svg = svg
        self.calls: list[str] = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if len(self.calls) <= self._fail_times:
            raise self._err("transient recraft outage")
        return self._svg


def test_add_layer_motif_freezes_a_valid_palette_color(fake_preview):
    # Regression: _freeze_motif must replace add_layer's "s0" placeholder color with a real
    # palette slot, else the frozen intent 422s after the gate.
    _offer_motif("bee", subject="bee", scope="whole")
    _set_author(BASE_MOTIF)
    _set_edit(
        [
            {
                "name": "add_layer",
                "args": {
                    "layer": {
                        "id": "extra",
                        "type": "motif",
                        "params": {"size_mm": 4},
                        "placement": {"type": "point_set", "point_set": {"points": [[10, 10]]}},
                    },
                    "motif": {"subject": "bee", "scope": "whole"},
                },
            }
        ]
    )
    sid = "add-layer"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "add a bee too"})
    resp = client.post(
        f"/api/v1/sessions/{sid}/select-motif", json={"layer_id": "extra", "motif_id": "bee"}
    )
    assert resp.status_code == 200, resp.text
    intent = _committed_intent(sid)
    extra = next(layer for layer in intent["layers"] if layer["id"] == "extra")
    assert extra["params"]["motif_id"] == "bee"
    slot_ids = {s["id"] for s in intent["palette"]["slots"]}
    assert extra["params"]["color"] in slot_ids  # a real palette slot, not "s0"


def test_select_motif_rejects_wrong_layer_without_consuming_gate(fake_preview):
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a bee", "subject": "bee", "scope": "whole"}}]
    )
    sid = "gate-wrong-layer"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use a bee"})
    bad = client.post(
        f"/api/v1/sessions/{sid}/select-motif",
        json={"layer_id": "other", "motif_id": "bee"},
    )
    assert bad.status_code == 409

    from app.sessions.graph import awaiting_gate

    assert awaiting_gate(sid) is True


def test_select_motif_rejects_existing_motif_not_in_active_candidates(fake_preview):
    _set_author(BASE_MOTIF)
    _set_edit(
        [
            {
                "name": "swap_motif",
                "args": {
                    "layer_id": "dots",
                    "description": "a new thing",
                    "subject": "new thing",
                    "scope": "whole",
                    "force_new": True,
                },
            }
        ]
    )
    sid = "gate-not-offered"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use a new thing"})
    bad = client.post(
        f"/api/v1/sessions/{sid}/select-motif",
        json={"layer_id": "dots", "motif_id": "bee"},
    )
    assert bad.status_code == 400


def test_gate_candidates_are_not_recomputed_on_resume(fake_preview, monkeypatch):
    import app.sessions.graph as sg

    calls = {"n": 0}

    def fake_present(*args, **kwargs):
        calls["n"] += 1
        return [{"motif_id": "bee", "similarity": None}]

    monkeypatch.setattr(sg, "present_candidates", fake_present)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a bee", "subject": "bee", "scope": "whole"}}]
    )
    sid = "gate-cached-candidates"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    pending = client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use a bee"})
    assert pending.status_code == 200, pending.text
    assert calls["n"] == 1

    selected = client.post(
        f"/api/v1/sessions/{sid}/select-motif",
        json={"layer_id": "dots", "motif_id": "bee"},
    )
    assert selected.status_code == 200, selected.text
    assert calls["n"] == 1


def test_text_motif_is_resolved_free_not_gated(fake_preview):
    # Regression: a text motif authored in a session must go through the free glyph builder,
    # never the Recraft confirm gate.
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    from app.adapters.llm import set_default_client

    set_default_client(
        _ScriptedLLM(
            json.dumps(
                {
                    "designs": [
                        {
                            "intent": BASE_TEXT,
                            "motif_specs": [
                                {"layer_id": "word", "text": "HELLO", "subject": "text", "scope": "whole"}
                            ],
                        }
                    ]
                }
            )
        )
    )
    resp = client.post("/api/v1/generate", json={"session_id": "text-1", "prompt": "the word hello"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "pending" not in body or body.get("pending") is None  # committed, not gated
    assert rec.calls == []  # text never touches Recraft


def test_select_unknown_motif_is_404_and_recoverable(fake_preview):
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _offer_motif("bee", subject="bee", scope="whole")
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a bee", "subject": "bee", "scope": "whole"}}]
    )
    sid = "bad-select"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use a bee"})
    bad = client.post(
        f"/api/v1/sessions/{sid}/select-motif", json={"layer_id": "dots", "motif_id": "nope"}
    )
    assert bad.status_code == 404  # bad client id → 4xx, not a 502, and not resumed
    assert rec.calls == []
    # the gate is still open → the user can recover with a valid motif
    good = client.post(
        f"/api/v1/sessions/{sid}/select-motif", json={"layer_id": "dots", "motif_id": "bee"}
    )
    assert good.status_code == 200, good.text


def test_gate_recovers_from_transient_recraft_failure(fake_preview):
    from app.adapters.recraft import set_default_recraft_client

    rec = _FlakyRecraft(fail_times=1, svg=_STAR_SVG)
    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "transient"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars"})

    first = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert first.status_code == 502  # transient outage surfaces
    # the turn is NOT wedged: retrying is accepted (not 409) once Recraft heals
    from app.sessions.graph import awaiting_gate

    assert awaiting_gate(sid) is True
    second = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "generate_motif"})
    assert second.status_code != 409
    assert len(rec.calls) == 2  # retried, not stuck replaying a cached error


def test_finalize_rejected_while_gate_pending(fake_preview):
    rec = _ScriptedRecraft(_STAR_SVG)
    from app.adapters.recraft import set_default_recraft_client

    set_default_recraft_client(rec)
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "swap_motif", "args": {"layer_id": "dots", "description": "a star", "subject": "star", "scope": "whole"}}]
    )
    sid = "fin-pending"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "use stars"})  # now paused at gate
    resp = client.post(f"/api/v1/sessions/{sid}/confirm", json={"action": "finalize"})
    assert resp.status_code == 409  # must not finalize a stale candidate mid-edit


# --- spec §6.3: one validation-failure re-prompt, then 422 --------------------


def _edit_client_calls() -> int:
    from app.adapters.edit_llm import get_default_edit_client

    return len(get_default_edit_client().calls)


def test_edit_validation_failure_is_retried_once_then_succeeds(fake_preview):
    # First tool call scales the motif past the tile (invalid); the retry (fed the error)
    # picks a valid factor → the turn commits. dots.size_mm=4, tile=48.
    _set_author(BASE_MOTIF)
    _set_edit(
        [{"name": "scale_motif", "args": {"layer_id": "dots", "factor": 20}}],  # 80 > 48 → invalid
        [{"name": "scale_motif", "args": {"layer_id": "dots", "factor": 0.5}}],  # 2 → valid
    )
    sid = "retry-ok"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    resp = client.post("/api/v1/generate", json={"session_id": sid, "prompt": "much bigger dots"})
    assert resp.status_code == 200, resp.text
    intent = _committed_intent(sid)
    assert (
        next(layer for layer in intent["layers"] if layer["id"] == "dots")["params"][
            "size_mm"
        ]
        == 2
    )
    assert _edit_client_calls() == 2  # proposed once, re-prompted once


def test_edit_validation_failure_after_retry_returns_422(fake_preview):
    _set_author(BASE_MOTIF)
    _set_edit([{"name": "scale_motif", "args": {"layer_id": "dots", "factor": 20}}])  # always invalid
    sid = "retry-422"
    client.post("/api/v1/generate", json={"session_id": sid, "prompt": "dots"})
    resp = client.post("/api/v1/generate", json={"session_id": sid, "prompt": "much bigger dots"})
    assert resp.status_code == 422
    assert "exceeds tile_mm" in str(resp.json()["detail"])
    assert _edit_client_calls() == 2  # initial + exactly one retry, then 422
    # the session is not wedged: current_intent is still the committed turn-1 intent
    assert (
        next(
            layer
            for layer in _committed_intent(sid)["layers"]
            if layer["id"] == "dots"
        )["params"]["size_mm"]
        == 4
    )
