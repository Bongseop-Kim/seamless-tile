"""Session-10 tests: prompt -> motif specs -> resolve (exact/hard-filter/generate) ->
inject -> engine. All LLM calls are scripted; no network, no DB."""

import json

import pytest
from fastapi.testclient import TestClient

import app.adapters.llm as llm_adapter
from app.adapters.base import AdapterClientError, AdapterResult
from app.adapters.llm import (
    build_intent as llm_build_intent,
    generate_motif_svg,
    set_default_client,
)
from app.adapters.motif_resolver import resolve_motifs
from app.main import app
from app.motifs.registry import MOTIFS, get_motif
from app.motifs.store import MotifRecord
from app.validate.intent import IntentInvalid
from tests.test_intent import mvp_intent

client = TestClient(app)

_GOOD_SVG = '<svg viewBox="0 0 12 12"><path d="M2 2 H10 V10 H2 Z" fill="currentColor"/></svg>'
_BAD_SVG = '<svg viewBox="0 0 12 12"><script>nope()</script></svg>'  # script => SanitizeError


@pytest.fixture(autouse=True)
def _clean():
    """Reset every process-global the glue touches, before and after each test."""

    def _purge():
        llm_adapter.clear_intent_cache()
        llm_adapter.clear_motif_svg_cache()
        set_default_client(None)
        for key in [k for k in MOTIFS if k.startswith("recraft-")]:
            del MOTIFS[key]

    _purge()
    yield
    _purge()


class _ScriptedLLM:
    """Returns canned completion strings in order (last one repeats)."""

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]


class _FakeStore:
    def __init__(self, *records: MotifRecord) -> None:
        self.rows = list(records)
        self.facet_queries: list[tuple] = []

    def upsert(self, record: MotifRecord) -> None:
        self.rows.append(record)

    def get(self, motif_id: str):
        return next((r for r in self.rows if r.id == motif_id), None)

    def all(self) -> list[MotifRecord]:
        return sorted(self.rows, key=lambda r: r.id)

    def find_by_facets(self, subject, part) -> list[MotifRecord]:
        self.facet_queries.append((subject, part))
        return sorted(
            (r for r in self.rows if r.subject == subject and r.part == part),
            key=lambda r: r.id,
        )


def _record(id_, subject, part, **facets) -> MotifRecord:
    return MotifRecord(
        id=id_,
        symbol=f'<symbol id="{id_}"><path d="M0 0H1V1H0Z"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject=subject,
        part=part,
        **facets,
    )


def _spec(layer_id="m", subject="pig", part="face", **extra) -> dict:
    return {"layer_id": layer_id, "subject": subject, "part": part, **extra}


# --- generate_motif_svg (miss path) ----------------------------------------


def test_generate_motif_svg_registers_and_is_deterministic():
    llm = _ScriptedLLM(_GOOD_SVG)
    mid1 = generate_motif_svg(_spec(), client=llm)
    mid2 = generate_motif_svg(_spec(), client=llm)  # same spec -> cache hit
    assert mid1 == mid2
    assert len(llm.calls) == 1  # second call served from the freeze cache
    assert get_motif(mid1).id == mid1  # registered in the in-memory registry


def test_generate_motif_svg_retries_once_then_succeeds():
    llm = _ScriptedLLM(_BAD_SVG, _GOOD_SVG)
    mid = generate_motif_svg(_spec(), client=llm, use_cache=False)
    assert len(llm.calls) == 2  # initial reject + one Tier-1 regeneration
    assert get_motif(mid).id == mid


def test_generate_motif_svg_exhausted_raises_client_error():
    llm = _ScriptedLLM(_BAD_SVG, _BAD_SVG)
    with pytest.raises(AdapterClientError):
        generate_motif_svg(_spec(), client=llm, use_cache=False)
    assert len(llm.calls) == 2  # exactly one retry, no more


# --- resolver: exact match / hard filter / miss -----------------------------


def test_resolver_exact_match_reuses_without_generating():
    rec = _record("recraft-aaa", "pig", "face", view="front", style="flat")
    store = _FakeStore(rec)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    out = resolve_motifs(
        intent, [_spec(view="front", style="flat")], store=store, llm_client=object()
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_hard_filter_hit_picks_lowest_id():
    # Two same-(subject,part) rows, neither an exact descriptor match -> lowest id wins.
    store = _FakeStore(
        _record("recraft-bbb", "pig", "face", view="side"),
        _record("recraft-aaa", "pig", "face", view="back"),
    )
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec(view="front")], store=store, llm_client=object())
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_miss_generates(monkeypatch):
    store = _FakeStore()  # empty -> miss
    llm = _ScriptedLLM(_GOOD_SVG)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec()], store=store, llm_client=llm)
    assert out["layers"][0]["params"]["motif_id"].startswith("recraft-")
    assert len(llm.calls) == 1


def test_resolver_store_none_always_misses_and_generates():
    llm = _ScriptedLLM(_GOOD_SVG)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec()], store=None, llm_client=llm)
    assert out["layers"][0]["params"]["motif_id"].startswith("recraft-")


def test_resolver_leaves_unspecced_layers_untouched():
    intent = {"layers": [{"id": "keep", "type": "motif", "params": {"motif_id": "bee", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec(layer_id="other")], store=_FakeStore(), llm_client=_ScriptedLLM(_GOOD_SVG))
    assert out["layers"][0]["params"]["motif_id"] == "bee"  # no matching spec -> unchanged


def test_resolver_empty_specs_is_noop_identity():
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "circle"}}]}
    assert resolve_motifs(intent, [], store=_FakeStore()) is intent


def test_resolver_normalizes_facets_for_reuse():
    # Differently-cased / padded facets must still reuse an existing motif (not
    # regenerate). llm_client=None means any generation attempt would raise, so a clean
    # return proves the normalized exact-match fired.
    rec = _record("recraft-aaa", "pig", "face", view="front")
    store = _FakeStore(rec)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    spec = {"layer_id": "m", "subject": "  Pig ", "part": "FACE", "view": "Front"}
    out = resolve_motifs(intent, [spec], store=store, llm_client=None)
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_missing_part_skips_query_and_generates():
    store = _FakeStore(_record("recraft-x", "pig", "face"))
    llm = _ScriptedLLM(_GOOD_SVG)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph"}}]}
    out = resolve_motifs(intent, [{"layer_id": "m", "subject": "pig"}], store=store, llm_client=llm)
    assert out["layers"][0]["params"]["motif_id"].startswith("recraft-")
    assert store.facet_queries == []  # query skipped: incomplete facets -> straight to generate


# --- build_intent: motif specs + facet validation ---------------------------


def _wrapped(intent: dict, specs: list[dict]) -> str:
    return json.dumps({"intent": intent, "motif_specs": specs})


def test_build_intent_returns_motif_specs():
    intent = mvp_intent()
    specs = [{"layer_id": "circle_on_stripe", "subject": "pig", "part": "face"}]
    llm = _ScriptedLLM(_wrapped(intent, specs))
    res = llm_build_intent("pig pattern", client=llm, use_cache=False)
    assert isinstance(res, AdapterResult)
    assert res.motif_specs == specs


def test_build_intent_bare_intent_has_no_specs():
    # Legacy/back-compat: a bare intent (no wrapper) still works, specs empty.
    llm = _ScriptedLLM(json.dumps(mvp_intent()))
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert res.motif_specs == []


def test_build_intent_out_of_vocab_part_reprompts_then_422():
    intent = mvp_intent()
    bad = [{"layer_id": "circle_on_stripe", "subject": "pig", "part": "nostril"}]  # not in PART_VOCAB
    llm = _ScriptedLLM(_wrapped(intent, bad), _wrapped(intent, bad))
    with pytest.raises(IntentInvalid):
        llm_build_intent("x", client=llm, use_cache=False)
    assert len(llm.calls) == 2  # initial + one re-prompt, then give up
    assert "nostril" in llm.calls[1] or "part" in llm.calls[1]  # vocab error fed back


def test_build_intent_out_of_vocab_then_valid_recovers():
    intent = mvp_intent()
    bad = [{"layer_id": "circle_on_stripe", "subject": "pig", "part": "nostril"}]
    good = [{"layer_id": "circle_on_stripe", "subject": "pig", "part": "face"}]
    llm = _ScriptedLLM(_wrapped(intent, bad), _wrapped(intent, good))
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert res.motif_specs == good
    assert len(llm.calls) == 2


# --- end-to-end route: prompt -> miss -> generate -> compose ----------------


def _miss_intent_and_specs():
    """An intent whose first motif layer is a placeholder to be resolved-on-miss; the
    bee layer references a built-in directly (no spec) and must stay untouched."""
    intent = mvp_intent()
    motif_layers = [l for l in intent["layers"] if l["type"] == "motif"]
    motif_layers[0]["params"]["motif_id"] = "pig"  # placeholder
    specs = [{"layer_id": motif_layers[0]["id"], "subject": "pig", "part": "face",
              "description": "smiling pig face"}]
    return intent, specs


def test_route_prompt_miss_generates_and_composes():
    intent, specs = _miss_intent_and_specs()
    set_default_client(_ScriptedLLM(_wrapped(intent, specs), _GOOD_SVG))
    resp = client.post("/api/v1/generate", json={"prompt": "돼지 무늬", "seed": 0})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidates"]
    # the resolved (concrete) motif id was injected into the candidate intent
    motif_ids = {
        l["params"]["motif_id"]
        for c in body["candidates"]
        for l in c["intent"]["layers"]
        if l["type"] == "motif"
    }
    assert any(m.startswith("recraft-") for m in motif_ids)  # generated motif present
    assert "bee" in motif_ids  # unspecced built-in layer preserved


def test_route_prompt_same_seed_is_deterministic():
    intent, specs = _miss_intent_and_specs()
    set_default_client(_ScriptedLLM(_wrapped(intent, specs), _GOOD_SVG))
    a = client.post("/api/v1/generate", json={"prompt": "돼지 무늬", "seed": 7})
    b = client.post("/api/v1/generate", json={"prompt": "돼지 무늬", "seed": 7})
    assert a.status_code == b.status_code == 200
    svgs_a = [c["svg"] for c in a.json()["candidates"]]
    svgs_b = [c["svg"] for c in b.json()["candidates"]]
    assert svgs_a == svgs_b  # byte-identical across repeats (caches + content-hash id)
