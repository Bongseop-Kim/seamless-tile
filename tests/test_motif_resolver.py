"""Session-10 tests: prompt -> motif specs -> resolve (exact/hard-filter/generate) ->
inject -> engine. All LLM calls are scripted; no network, no DB."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.adapters.embedding as emb_adapter
import app.adapters.llm as llm_adapter
import app.adapters.motif_resolver as motif_resolver
import app.adapters.recraft as recraft_adapter
from app.adapters.base import AdapterClientError, AdapterResult
from app.adapters.llm import (
    build_intent as llm_build_intent,
    generate_motif_svg,
    set_default_client,
)
from app.adapters.motif_resolver import resolve_motifs
from app.main import app
from app.motifs import store as store_mod
from app.motifs.registry import MOTIFS, get_motif
from app.motifs.store import MotifRecord, MotifStoreError
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
        emb_adapter.clear_embedding_cache()
        emb_adapter.set_default_embedding_client(None)
        recraft_adapter.clear_motif_cache()
        recraft_adapter.clear_recraft_motif_cache()
        recraft_adapter.set_default_recraft_client(None)
        store_mod.clear_default_store()
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

    def find_by_variant_group(self, variant_group, *, status="curated"):
        return sorted(
            (
                r
                for r in self.rows
                if r.variant_group == variant_group and r.status == status
            ),
            key=lambda r: r.id,
        )


class _FixedEmbed:
    """Embedding client returning a fixed vector regardless of text (model id fixed)."""

    def __init__(self, vector, model="fake-embed") -> None:
        self.model = model
        self.vector = list(vector)

    def embed(self, text: str) -> list[float]:
        return list(self.vector)


def _set_tau(monkeypatch, tau: float) -> None:
    monkeypatch.setattr(
        motif_resolver, "get_settings", lambda: SimpleNamespace(motif_similarity_tau=tau)
    )


def _layer_intent() -> dict:
    return {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}


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


# --- S11: embedding soft similarity (τ gate) --------------------------------


def test_resolver_soft_similarity_reuses_above_tau(monkeypatch):
    # Not an exact match (view differs) -> soft path. cos=1.0 >= τ -> reuse.
    # llm_client=None: any generation attempt would raise, so a clean return proves reuse.
    _set_tau(monkeypatch, 0.6)
    rec = _record("recraft-sim", "pig", "face", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=store, llm_client=None, embedding_client=_FixedEmbed([1.0, 0.0]),
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-sim"


def test_resolver_soft_similarity_generates_below_tau(monkeypatch):
    _set_tau(monkeypatch, 0.6)
    rec = _record("recraft-sim", "pig", "face", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    llm = _ScriptedLLM(_GOOD_SVG)
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=store, llm_client=llm, embedding_client=_FixedEmbed([0.0, 1.0]),  # cos=0.0
    )
    assert out["layers"][0]["params"]["motif_id"].startswith("recraft-")
    assert len(llm.calls) == 1  # below τ -> generated


def test_resolver_tau_boundary(monkeypatch):
    # query vs candidate cosine is exactly 0.7.
    rec = lambda: _record("recraft-sim", "pig", "face", view="side", embedding=[1.0, 0.0])
    query = [0.7, 0.714142842854285]  # |query| == 1.0, dot with [1,0] == 0.7

    _set_tau(monkeypatch, 0.7)  # 0.7 >= 0.7 -> reuse
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=_FakeStore(rec()), llm_client=None, embedding_client=_FixedEmbed(query),
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-sim"

    _set_tau(monkeypatch, 0.71)  # 0.7 < 0.71 -> generate
    llm = _ScriptedLLM(_GOOD_SVG)
    resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=_FakeStore(rec()), llm_client=llm, embedding_client=_FixedEmbed(query),
    )
    assert len(llm.calls) == 1


def test_resolver_embedding_unconfigured_falls_back_to_lowest_id():
    # No embedding client -> embed_query returns None -> S10 hard-filter reuse (lowest id).
    rec = _record("recraft-aaa", "pig", "face", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=store, llm_client=None, embedding_client=None,
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_embedding_error_is_fail_soft(monkeypatch):
    _set_tau(monkeypatch, 0.6)

    class _BoomEmbed:
        model = "m"

        def embed(self, text):
            raise AdapterClientError("embed upstream down")

    rec = _record("recraft-aaa", "pig", "face", view="side", embedding=[1.0, 0.0])
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=_FakeStore(rec), llm_client=None, embedding_client=_BoomEmbed(),
    )
    # fail-soft: reuse (not a 502, not a regenerate)
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_dimension_mismatch_excluded_falls_back(monkeypatch):
    _set_tau(monkeypatch, 0.6)
    # candidate embedding dim (3) != query dim (2) -> excluded -> S10 fallback reuse.
    rec = _record("recraft-aaa", "pig", "face", view="side", embedding=[1.0, 0.0, 0.0])
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=_FakeStore(rec), llm_client=None, embedding_client=_FixedEmbed([1.0, 0.0]),
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_miss_persists_query_embedding(monkeypatch):
    _set_tau(monkeypatch, 0.99)  # force a miss despite a hard-filter candidate
    rec = _record("recraft-other", "pig", "face", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    store_mod.set_default_store(store)  # generation write-through targets the default store
    llm = _ScriptedLLM(_GOOD_SVG)
    query = [0.0, 1.0]
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=store, llm_client=llm, embedding_client=_FixedEmbed(query),
    )
    new_id = out["layers"][0]["params"]["motif_id"]
    assert len(llm.calls) == 1  # generated
    stored = store.get(new_id)
    assert stored is not None and stored.embedding == query


def test_resolver_hit_samples_from_curated_pool(monkeypatch):
    # Two curated variants in one group; seed varies which variant the hit resolves to.
    _set_tau(monkeypatch, 0.6)
    grp = "grp1"
    r1 = _record("recraft-aaa", "pig", "face", view="side",
                 variant_group=grp, status="curated", embedding=[1.0, 0.0])
    r2 = _record("recraft-bbb", "pig", "face", view="side",
                 variant_group=grp, status="curated", embedding=[1.0, 0.0])
    store = _FakeStore(r1, r2)
    chosen = {
        resolve_motifs(
            _layer_intent(), [_spec(view="front")],
            store=store, llm_client=None, embedding_client=_FixedEmbed([1.0, 0.0]), seed=s,
        )["layers"][0]["params"]["motif_id"]
        for s in range(20)
    }
    assert chosen == {"recraft-aaa", "recraft-bbb"}  # criterion 3 through the full resolver


def test_resolver_same_inputs_deterministic(monkeypatch):
    # Criterion 2 (in-process): same prompt+seed+registry_version -> same resolved id.
    _set_tau(monkeypatch, 0.6)
    rec = _record("recraft-sim", "pig", "face", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    emb = _FixedEmbed([1.0, 0.0])
    a = resolve_motifs(_layer_intent(), [_spec(view="front")],
                       store=store, llm_client=None, embedding_client=emb, seed=3)
    b = resolve_motifs(_layer_intent(), [_spec(view="front")],
                       store=store, llm_client=None, embedding_client=emb, seed=3)
    assert a == b


def test_resolver_variant_pool_query_error_falls_back_to_match():
    # A flaky pool query must not break the request: fall back to the matched motif.
    class _PoolBoomStore(_FakeStore):
        def find_by_variant_group(self, variant_group, *, status="curated"):
            raise MotifStoreError("variant-group query down")

    rec = _record("recraft-aaa", "pig", "face", view="front", variant_group="g1")
    store = _PoolBoomStore(rec)
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],  # exact match -> hit -> variant pool path
        store=store, llm_client=None, embedding_client=None,
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


# --- S13: complexity-based generation-source routing (D8/D11) ----------------


class _FakeRecraft:
    """Returns a fixed SVG; counts calls so routing is observable."""

    def __init__(self, svg: str) -> None:
        self._svg = svg
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self._svg


# Two solid colors -> a multicolor motif only the Recraft path can produce here.
_RECRAFT_SVG = (
    '<svg viewBox="0 0 12 12">'
    '<rect x="0" y="0" width="6" height="12" fill="#ff0000"/>'
    '<rect x="6" y="0" width="6" height="12" fill="#0000ff"/></svg>'
)


def test_resolver_routes_detailed_to_recraft():
    llm = _ScriptedLLM(_GOOD_SVG)
    rc = _FakeRecraft(_RECRAFT_SVG)
    out = resolve_motifs(
        _layer_intent(), [_spec(complexity="detailed")],
        store=_FakeStore(), llm_client=llm, recraft_client=rc,
    )
    mid = out["layers"][0]["params"]["motif_id"]
    assert mid.startswith("recraft-")
    assert rc.calls == 1 and len(llm.calls) == 0  # routed to Recraft, not the LLM
    assert get_motif(mid).color_slots == ("s0", "s1")  # multicolor slots preserved


def test_resolver_routes_simple_to_llm():
    llm = _ScriptedLLM(_GOOD_SVG)
    rc = _FakeRecraft(_RECRAFT_SVG)
    resolve_motifs(
        _layer_intent(), [_spec(complexity="simple")],
        store=_FakeStore(), llm_client=llm, recraft_client=rc,
    )
    assert len(llm.calls) == 1 and rc.calls == 0


def test_resolver_defaults_missing_complexity_to_llm():
    llm = _ScriptedLLM(_GOOD_SVG)
    rc = _FakeRecraft(_RECRAFT_SVG)
    resolve_motifs(
        _layer_intent(), [_spec()],  # no complexity hint
        store=_FakeStore(), llm_client=llm, recraft_client=rc,
    )
    assert len(llm.calls) == 1 and rc.calls == 0


def test_resolver_source_override_forces_recraft():
    llm = _ScriptedLLM(_GOOD_SVG)
    rc = _FakeRecraft(_RECRAFT_SVG)
    resolve_motifs(  # simple complexity, but explicit override wins
        _layer_intent(), [_spec(complexity="simple", source="recraft")],
        store=_FakeStore(), llm_client=llm, recraft_client=rc,
    )
    assert rc.calls == 1 and len(llm.calls) == 0


def test_resolver_source_override_forces_llm():
    llm = _ScriptedLLM(_GOOD_SVG)
    rc = _FakeRecraft(_RECRAFT_SVG)
    resolve_motifs(  # detailed complexity, but explicit override wins
        _layer_intent(), [_spec(complexity="detailed", source="llm")],
        store=_FakeStore(), llm_client=llm, recraft_client=rc,
    )
    assert len(llm.calls) == 1 and rc.calls == 0


def test_resolver_detailed_without_recraft_client_raises_502_class():
    # Recraft unset + detailed routing -> RecraftNotConfigured (AdapterClientError -> 502).
    with pytest.raises(AdapterClientError):
        resolve_motifs(
            _layer_intent(), [_spec(complexity="detailed")],
            store=_FakeStore(), llm_client=_ScriptedLLM(_GOOD_SVG), recraft_client=None,
        )
