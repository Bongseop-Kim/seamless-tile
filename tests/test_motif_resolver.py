"""Session-10 tests: prompt -> motif specs -> resolve (exact/hard-filter/generate) ->
inject -> engine. All external calls are scripted; no network, no DB. The miss path
generates via Recraft (the LLM direct-painting branch was removed)."""

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
    set_default_client,
)
from app.adapters.motif_resolver import resolve_motifs
import app.api.routes.generate as gen_route
from app.main import app
from app.motifs import store as store_mod
from app.motifs.registry import MOTIFS, get_motif
from app.motifs.store import MotifMatch, MotifMeta, MotifRecord, MotifStoreError
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


from tests._fakes import _ScriptedLLM


class _ScriptedRecraft:
    """Returns canned SVGs in order (last repeats); records calls. Mirrors _ScriptedLLM
    but exposes the RecraftClient ``.generate(prompt)`` seam the miss path drives."""

    def __init__(self, *svgs: str) -> None:
        if not svgs:
            raise ValueError("_ScriptedRecraft requires at least one SVG")
        self._svgs = list(svgs)
        self.calls: list[str] = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._svgs[min(len(self.calls) - 1, len(self._svgs) - 1)]


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(x) * float(x) for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(float(x) * float(y) for x, y in zip(a, b)) / (na * nb)


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

    def find_facets_meta(self, scope) -> list[MotifMeta]:
        self.facet_queries.append(scope)
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
            for r in sorted(
                (r for r in self.rows if r.scope == scope), key=lambda r: r.id
            )
        ]

    def find_best_by_embedding(self, scope, query_vec) -> MotifMatch | None:
        # Reference Python cosine: the DB-side `<=>` query must agree with this (the
        # live PG parity test asserts it). Same dim guard + lowest-id tie-break as the
        # old resolver scan.
        best = None  # (rec, sim)
        for r in sorted((r for r in self.rows if r.scope == scope), key=lambda r: r.id):
            emb = r.embedding
            if not emb or len(emb) != len(query_vec):
                continue
            sim = _cosine(query_vec, emb)
            if best is None or sim > best[1]:
                best = (r, sim)
        if best is None:
            return None
        rec, sim = best
        return MotifMatch(id=rec.id, variant_group=rec.variant_group, similarity=sim)

    def find_by_variant_group(self, variant_group):
        return sorted(
            (
                r
                for r in self.rows
                if r.variant_group == variant_group
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


def _record(id_, subject, scope, **facets) -> MotifRecord:
    return MotifRecord(
        id=id_,
        symbol=f'<symbol id="{id_}"><path d="M0 0H1V1H0Z"/></symbol>',
        bbox_mm=(-0.5, -0.5, 0.5, 0.5),
        anchor=(0.0, 0.0),
        subject=subject,
        scope=scope,
        **facets,
    )


def _spec(layer_id="m", subject="pig", scope="partial", **extra) -> dict:
    return {"layer_id": layer_id, "subject": subject, "scope": scope, **extra}


# --- resolver: exact match / hard filter / miss -----------------------------


def test_resolver_exact_match_reuses_without_generating():
    rec = _record("recraft-aaa", "pig", "partial", view="front", style="flat")
    store = _FakeStore(rec)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec(view="front", style="flat")], store=store)
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_hard_filter_hit_picks_lowest_id():
    # Two same-(subject,scope) rows, neither an exact descriptor match -> lowest id wins.
    store = _FakeStore(
        _record("recraft-bbb", "pig", "partial", view="side"),
        _record("recraft-aaa", "pig", "partial", view="back"),
    )
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec(view="front")], store=store)
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_miss_generates():
    store = _FakeStore()  # empty -> miss
    rc = _ScriptedRecraft(_GOOD_SVG)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec()], store=store, recraft_client=rc)
    assert out["layers"][0]["params"]["motif_id"].startswith("recraft-")
    assert len(rc.calls) == 1


def test_resolver_store_none_always_misses_and_generates():
    rc = _ScriptedRecraft(_GOOD_SVG)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec()], store=None, recraft_client=rc)
    assert out["layers"][0]["params"]["motif_id"].startswith("recraft-")


def test_resolver_leaves_unspecced_layers_untouched():
    intent = {"layers": [{"id": "keep", "type": "motif", "params": {"motif_id": "bee", "color": "a"}}]}
    out = resolve_motifs(intent, [_spec(layer_id="other")], store=_FakeStore())
    assert out["layers"][0]["params"]["motif_id"] == "bee"  # no matching spec -> unchanged


def test_resolver_empty_specs_is_noop_identity():
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "circle"}}]}
    assert resolve_motifs(intent, [], store=_FakeStore()) is intent


def test_resolver_normalizes_facets_for_reuse():
    # Differently-cased / padded facets must still reuse an existing motif (not
    # regenerate). No recraft client means any generation attempt would raise, so a clean
    # return proves the normalized exact-match fired.
    rec = _record("recraft-aaa", "pig", "partial", view="front")
    store = _FakeStore(rec)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph", "color": "a"}}]}
    spec = {"layer_id": "m", "subject": "  Pig ", "scope": "Partial", "view": "Front"}
    out = resolve_motifs(intent, [spec], store=store)
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_missing_scope_skips_query_and_generates():
    store = _FakeStore(_record("recraft-x", "pig", "partial"))
    rc = _ScriptedRecraft(_GOOD_SVG)
    intent = {"layers": [{"id": "m", "type": "motif", "params": {"motif_id": "ph"}}]}
    out = resolve_motifs(intent, [{"layer_id": "m", "subject": "pig"}], store=store, recraft_client=rc)
    assert out["layers"][0]["params"]["motif_id"].startswith("recraft-")
    assert store.facet_queries == []  # query skipped: incomplete facets -> straight to generate


# --- build_intent: motif specs + facet validation ---------------------------


def _wrapped(intent: dict, specs: list[dict]) -> str:
    return json.dumps({"intent": intent, "motif_specs": specs})


def test_build_intent_returns_motif_specs():
    intent = mvp_intent()
    specs = [{"layer_id": "circle_on_stripe", "subject": "pig", "scope": "partial"}]
    llm = _ScriptedLLM(_wrapped(intent, specs))
    res = llm_build_intent("pig pattern", client=llm, use_cache=False)
    assert isinstance(res, AdapterResult)
    assert res.motif_specs == specs


def test_build_intent_bare_intent_has_no_specs():
    # Legacy/back-compat: a bare intent (no wrapper) still works, specs empty.
    llm = _ScriptedLLM(json.dumps(mvp_intent()))
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert res.motif_specs == []


def test_build_intent_out_of_vocab_scope_reprompts_then_422():
    intent = mvp_intent()
    bad = [{"layer_id": "circle_on_stripe", "subject": "pig", "scope": "bogus"}]  # not in SCOPE_VOCAB
    llm = _ScriptedLLM(_wrapped(intent, bad), _wrapped(intent, bad))
    with pytest.raises(IntentInvalid):
        llm_build_intent("x", client=llm, use_cache=False)
    assert len(llm.calls) == 2  # initial + one re-prompt, then give up
    assert "bogus" in llm.calls[1] or "scope" in llm.calls[1]  # vocab error fed back


def test_build_intent_out_of_vocab_then_valid_recovers():
    intent = mvp_intent()
    bad = [{"layer_id": "circle_on_stripe", "subject": "pig", "scope": "bogus"}]
    good = [{"layer_id": "circle_on_stripe", "subject": "pig", "scope": "whole"}]
    llm = _ScriptedLLM(_wrapped(intent, bad), _wrapped(intent, good))
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert res.motif_specs == good
    assert len(llm.calls) == 2


# --- end-to-end route: prompt -> miss -> generate -> compose ----------------


def _miss_intent_and_specs():
    """An intent whose first motif layer is a placeholder to be resolved-on-miss; the
    bee layer references a built-in directly (no spec) and must stay untouched."""
    intent = mvp_intent()
    motif_layers = [layer for layer in intent["layers"] if layer["type"] == "motif"]
    motif_layers[0]["params"]["motif_id"] = "pig"  # placeholder
    specs = [{"layer_id": motif_layers[0]["id"], "subject": "pig", "scope": "partial",
              "description": "smiling pig face"}]
    return intent, specs


def test_route_prompt_miss_generates_and_composes(monkeypatch):
    intent, specs = _miss_intent_and_specs()
    set_default_client(_ScriptedLLM(_wrapped(intent, specs)))
    recraft_adapter.set_default_recraft_client(_ScriptedRecraft(_GOOD_SVG))
    captured: list = []
    monkeypatch.setattr(
        gen_route, "insert_generation_log", lambda row: captured.append(row)
    )
    resp = client.post("/api/v1/generate", json={"prompt": "돼지 무늬", "seed": 0})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidates"]
    assert captured, "expected a generation log row"
    # the resolved (concrete) motif id was injected into the logged (resolved) intent
    motif_ids = {
        layer["params"]["motif_id"]
        for layer in captured[0].intent["designs"][0]["layers"]
        if layer["type"] == "motif"
    }
    assert any(m.startswith("recraft-") for m in motif_ids)  # generated motif present
    assert "bee" in motif_ids  # unspecced motif layer (fixture id) left untouched


def test_route_prompt_same_seed_is_deterministic(monkeypatch):
    intent, specs = _miss_intent_and_specs()
    set_default_client(_ScriptedLLM(_wrapped(intent, specs)))
    recraft_adapter.set_default_recraft_client(_ScriptedRecraft(_GOOD_SVG))
    captured: list = []
    monkeypatch.setattr(
        gen_route, "insert_generation_log", lambda row: captured.append(row)
    )
    a = client.post("/api/v1/generate", json={"prompt": "돼지 무늬", "seed": 7})
    b = client.post("/api/v1/generate", json={"prompt": "돼지 무늬", "seed": 7})
    assert a.status_code == b.status_code == 200
    assert len(captured) == 2
    # the byte-identical SVG is preserved in the log row (no longer in the response)
    svgs_a = [c["svg"] for c in captured[0].candidates]
    svgs_b = [c["svg"] for c in captured[1].candidates]
    assert svgs_a == svgs_b  # byte-identical across repeats (caches + content-hash id)


# --- S11: embedding soft similarity (τ gate) --------------------------------


def test_resolver_soft_similarity_reuses_above_tau(monkeypatch):
    # Not an exact match (view differs) -> soft path. cos=1.0 >= τ -> reuse.
    # No recraft client: any generation attempt would raise, so a clean return proves reuse.
    _set_tau(monkeypatch, 0.6)
    rec = _record("recraft-sim", "pig", "partial", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=store, embedding_client=_FixedEmbed([1.0, 0.0]),
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-sim"


def test_resolver_soft_similarity_generates_below_tau(monkeypatch):
    _set_tau(monkeypatch, 0.6)
    rec = _record("recraft-sim", "pig", "partial", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    rc = _ScriptedRecraft(_GOOD_SVG)
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=store, recraft_client=rc, embedding_client=_FixedEmbed([0.0, 1.0]),  # cos=0.0
    )
    assert out["layers"][0]["params"]["motif_id"].startswith("recraft-")
    assert len(rc.calls) == 1  # below τ -> generated


def test_resolver_tau_boundary(monkeypatch):
    # query vs candidate cosine is exactly 0.7.
    def rec():
        return _record("recraft-sim", "pig", "partial", view="side", embedding=[1.0, 0.0])
    query = [0.7, 0.714142842854285]  # |query| == 1.0, dot with [1,0] == 0.7

    _set_tau(monkeypatch, 0.7)  # 0.7 >= 0.7 -> reuse
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=_FakeStore(rec()), embedding_client=_FixedEmbed(query),
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-sim"

    _set_tau(monkeypatch, 0.71)  # 0.7 < 0.71 -> generate
    rc = _ScriptedRecraft(_GOOD_SVG)
    resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=_FakeStore(rec()), recraft_client=rc, embedding_client=_FixedEmbed(query),
    )
    assert len(rc.calls) == 1


def test_resolver_embedding_unconfigured_falls_back_to_lowest_id():
    # No embedding client -> embed_query returns None -> S10 hard-filter reuse (lowest id).
    rec = _record("recraft-aaa", "pig", "partial", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=store, embedding_client=None,
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_embedding_call_failure_propagates(monkeypatch):
    # A real embedding-CALL failure (client present, upstream down) must NOT silently reuse
    # an arbitrary motif — it propagates AdapterClientError so the route maps it to 502.
    # (Distinct from *unconfigured* embedding, which embed_query returns None for; that path
    # stays graceful — see test_resolver_embedding_unconfigured_falls_back_to_lowest_id.)
    _set_tau(monkeypatch, 0.6)

    class _BoomEmbed:
        model = "m"

        def embed(self, text):
            raise AdapterClientError("embed upstream down")

    rec = _record("recraft-aaa", "pig", "partial", view="side", embedding=[1.0, 0.0])
    with pytest.raises(AdapterClientError):
        resolve_motifs(
            _layer_intent(), [_spec(view="front")],
            store=_FakeStore(rec), embedding_client=_BoomEmbed(),
        )


def test_resolver_dimension_mismatch_excluded_falls_back(monkeypatch):
    _set_tau(monkeypatch, 0.6)
    # candidate embedding dim (3) != query dim (2) -> excluded -> S10 fallback reuse.
    rec = _record("recraft-aaa", "pig", "partial", view="side", embedding=[1.0, 0.0, 0.0])
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=_FakeStore(rec), embedding_client=_FixedEmbed([1.0, 0.0]),
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


def test_resolver_miss_persists_query_embedding(monkeypatch):
    _set_tau(monkeypatch, 0.99)  # force a miss despite a hard-filter candidate
    rec = _record("recraft-other", "pig", "partial", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    store_mod.set_default_store(store)  # generation write-through targets the default store
    rc = _ScriptedRecraft(_GOOD_SVG)
    query = [0.0, 1.0]
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],
        store=store, recraft_client=rc, embedding_client=_FixedEmbed(query),
    )
    new_id = out["layers"][0]["params"]["motif_id"]
    assert len(rc.calls) == 1  # generated
    stored = store.get(new_id)
    assert stored is not None and stored.embedding == query


def test_resolver_hit_samples_from_reusable_pool(monkeypatch):
    # Two reusable variants in one group; seed varies which variant the hit resolves to.
    _set_tau(monkeypatch, 0.6)
    grp = "grp1"
    r1 = _record("recraft-aaa", "pig", "partial", view="side",
                 variant_group=grp, embedding=[1.0, 0.0])
    r2 = _record("recraft-bbb", "pig", "partial", view="side",
                 variant_group=grp, embedding=[1.0, 0.0])
    store = _FakeStore(r1, r2)
    chosen = {
        resolve_motifs(
            _layer_intent(), [_spec(view="front")],
            store=store, embedding_client=_FixedEmbed([1.0, 0.0]), seed=s,
        )["layers"][0]["params"]["motif_id"]
        for s in range(20)
    }
    assert chosen == {"recraft-aaa", "recraft-bbb"}  # criterion 3 through the full resolver


def test_resolver_part_collision_excluded_from_pool_vector(monkeypatch):
    # Bug: a "giraffe leg" and a "giraffe face" share (subject, scope) -> one
    # variant_group (intentional, for petal-style diversity). The part lives only in
    # `description`, so the coarse pool used to let the leg be sampled for a face query.
    # Spec description differs from both rows -> vector path; the embedding match picks
    # the face and the pool must now drop the dissimilar leg for every seed.
    _set_tau(monkeypatch, 0.6)
    grp = "giraffe-partial"
    leg = _record("recraft-aaa-leg", "giraffe", "partial", description="giraffe leg",
                  variant_group=grp, embedding=[0.0, 1.0])  # lower id than the face
    face = _record("recraft-bbb-face", "giraffe", "partial", description="giraffe face",
                   variant_group=grp, embedding=[1.0, 0.0])
    store = _FakeStore(leg, face)
    chosen = {
        resolve_motifs(
            _layer_intent(),
            [_spec(subject="giraffe", description="giraffe head")],  # != stored -> vector
            store=store, embedding_client=_FixedEmbed([1.0, 0.0]), seed=s,
        )["layers"][0]["params"]["motif_id"]
        for s in range(20)
    }
    assert chosen == {"recraft-bbb-face"}  # leg (cos 0.0 < τ) never sampled


def test_resolver_part_collision_excluded_from_pool_exact(monkeypatch):
    # Same collision via the exact path: the spec's description exactly matches the face.
    # `description` is now an exact-descriptor facet, so the exact anchor is the face (not
    # the lower-id leg), and the τ-scoped pool still drops the leg.
    _set_tau(monkeypatch, 0.6)
    grp = "giraffe-partial"
    leg = _record("recraft-aaa-leg", "giraffe", "partial", description="giraffe leg",
                  variant_group=grp, embedding=[0.0, 1.0])  # lower id: old exact-match bug
    face = _record("recraft-bbb-face", "giraffe", "partial", description="giraffe face",
                   variant_group=grp, embedding=[1.0, 0.0])
    store = _FakeStore(leg, face)
    chosen = {
        resolve_motifs(
            _layer_intent(),
            [_spec(subject="giraffe", description="giraffe face")],  # == face -> exact
            store=store, embedding_client=_FixedEmbed([1.0, 0.0]), seed=s,
        )["layers"][0]["params"]["motif_id"]
        for s in range(20)
    }
    assert chosen == {"recraft-bbb-face"}


def test_resolver_same_inputs_deterministic(monkeypatch):
    # Criterion 2 (in-process): same prompt+seed+registry_version -> same resolved id.
    _set_tau(monkeypatch, 0.6)
    rec = _record("recraft-sim", "pig", "partial", view="side", embedding=[1.0, 0.0])
    store = _FakeStore(rec)
    emb = _FixedEmbed([1.0, 0.0])
    a = resolve_motifs(_layer_intent(), [_spec(view="front")],
                       store=store, embedding_client=emb, seed=3)
    b = resolve_motifs(_layer_intent(), [_spec(view="front")],
                       store=store, embedding_client=emb, seed=3)
    assert a == b


def test_resolver_variant_pool_query_error_falls_back_to_match():
    # A flaky pool query must not break the request: fall back to the matched motif.
    class _PoolBoomStore(_FakeStore):
        def find_by_variant_group(self, variant_group):
            raise MotifStoreError("variant-group query down")

    rec = _record("recraft-aaa", "pig", "partial", view="front", variant_group="g1")
    store = _PoolBoomStore(rec)
    out = resolve_motifs(
        _layer_intent(), [_spec(view="front")],  # exact match -> hit -> variant pool path
        store=store, embedding_client=None,
    )
    assert out["layers"][0]["params"]["motif_id"] == "recraft-aaa"


# --- miss-path generation via Recraft ----------------------------------------


# Two solid colors -> a multicolor motif the Recraft path produces here.
_RECRAFT_SVG = (
    '<svg viewBox="0 0 12 12">'
    '<rect x="0" y="0" width="6" height="12" fill="#ff0000"/>'
    '<rect x="6" y="0" width="6" height="12" fill="#0000ff"/></svg>'
)


def test_resolver_miss_generates_multicolor_via_recraft():
    rc = _ScriptedRecraft(_RECRAFT_SVG)
    out = resolve_motifs(
        _layer_intent(), [_spec()],
        store=_FakeStore(), recraft_client=rc,
    )
    mid = out["layers"][0]["params"]["motif_id"]
    assert mid.startswith("recraft-")
    assert len(rc.calls) == 1
    assert get_motif(mid).color_slots == ("s0", "s1")  # multicolor slots preserved


def test_resolver_miss_without_recraft_client_raises_502_class():
    # A miss with no Recraft client -> RecraftNotConfigured (AdapterClientError -> 502).
    with pytest.raises(AdapterClientError):
        resolve_motifs(
            _layer_intent(), [_spec()],
            store=_FakeStore(), recraft_client=None,
        )


# --- §6.4 Tier-1 gate: drop -> partial success / all-fail -> 502 -------------


def _multi_motif_intent() -> dict:
    return {
        "layers": [
            {"id": "m1", "type": "motif", "params": {"color": "a"}},
            {"id": "m2", "type": "motif", "params": {"color": "b"}},
        ]
    }


def test_resolver_partial_success_drops_failed_motif():
    # m1 generates clean (1 call), m2 exhausts the gate (2 calls) -> m2 dropped, 200-class.
    warnings: list[str] = []
    out = resolve_motifs(
        _multi_motif_intent(),
        [
            _spec(layer_id="m1", subject="pig", scope="partial"),
            _spec(layer_id="m2", subject="bee", scope="partial"),
        ],
        store=_FakeStore(),
        recraft_client=_ScriptedRecraft(_GOOD_SVG, _BAD_SVG),
        warnings=warnings,
    )
    assert [layer["id"] for layer in out["layers"]] == ["m1"]  # m2 dropped
    assert out["layers"][0]["params"]["motif_id"]  # m1 resolved
    assert any("m2" in w and "bee/partial" in w for w in warnings)


def test_resolver_all_motifs_fail_raises_502_class():
    with pytest.raises(AdapterClientError):
        resolve_motifs(
            _multi_motif_intent(),
            [
                _spec(layer_id="m1", subject="pig", scope="partial"),
                _spec(layer_id="m2", subject="bee", scope="partial"),
            ],
            store=_FakeStore(),
            recraft_client=_ScriptedRecraft(_BAD_SVG, _BAD_SVG),
        )


def test_resolver_single_failure_without_warnings_kwarg_raises():
    # Back-compat: the new partial path doesn't crash when no warnings sink is passed,
    # and a sole failing motif still raises (all-fail -> 502).
    with pytest.raises(AdapterClientError):
        resolve_motifs(
            _layer_intent(),
            [_spec()],
            store=_FakeStore(),
            recraft_client=_ScriptedRecraft(_BAD_SVG, _BAD_SVG),
        )


def test_resolver_cascade_drops_dependent_layer():
    # p1 hosts on m_bad (no spec of its own); when m_bad is dropped p1 cascades out,
    # while the unrelated m_ok survives.
    intent = {
        "layers": [
            {"id": "m_ok", "type": "motif", "params": {"color": "a"}},
            {"id": "m_bad", "type": "motif", "params": {"color": "b"}},
            {
                "id": "p1",
                "type": "motif",
                "params": {"color": "c"},
                "placement": {"type": "path_following", "host_layer": "m_bad"},
            },
        ]
    }
    warnings: list[str] = []
    out = resolve_motifs(
        intent,
        [
            _spec(layer_id="m_ok", subject="pig", scope="partial"),
            _spec(layer_id="m_bad", subject="bee", scope="partial"),
        ],
        store=_FakeStore(),
        recraft_client=_ScriptedRecraft(_GOOD_SVG, _BAD_SVG),
        warnings=warnings,
    )
    assert [layer["id"] for layer in out["layers"]] == ["m_ok"]
    assert any("m_bad" in w and "Tier-1" in w for w in warnings)
    assert any("p1" in w and "host_layer" in w for w in warnings)


def test_resolver_cascade_to_empty_raises_502_class():
    # m_ok resolves but hosts on m_bad; m_bad fails -> cascade removes m_ok too ->
    # no survivors -> 502 (not 422).
    intent = {
        "layers": [
            {
                "id": "m_ok",
                "type": "motif",
                "params": {"color": "a"},
                "placement": {"host_layer": "m_bad"},
            },
            {"id": "m_bad", "type": "motif", "params": {"color": "b"}},
        ]
    }
    with pytest.raises(AdapterClientError):
        resolve_motifs(
            intent,
            [
                _spec(layer_id="m_ok", subject="pig", scope="partial"),
                _spec(layer_id="m_bad", subject="bee", scope="partial"),
            ],
            store=_FakeStore(),
            recraft_client=_ScriptedRecraft(_GOOD_SVG, _BAD_SVG),
        )


def test_resolver_partial_success_is_deterministic():
    def run():
        recraft_adapter.clear_recraft_motif_cache()  # same starting state both runs (no cache leak)
        warnings: list[str] = []
        out = resolve_motifs(
            _multi_motif_intent(),
            [
                _spec(layer_id="m1", subject="pig", scope="partial"),
                _spec(layer_id="m2", subject="bee", scope="partial"),
            ],
            store=_FakeStore(),
            recraft_client=_ScriptedRecraft(_GOOD_SVG, _BAD_SVG),
            warnings=warnings,
        )
        return out, warnings

    out1, w1 = run()
    out2, w2 = run()
    assert out1 == out2
    assert w1 == w2


def test_route_prompt_partial_success_returns_200_with_warning():
    # Two spec'd motif layers: the first resolves, the second exhausts the gate and is
    # dropped -> the request still succeeds (partial success) with a drop warning.
    intent, specs = _miss_intent_and_specs()  # motif_layers[0] -> valid placeholder + spec
    motif_layers = [layer for layer in intent["layers"] if layer["type"] == "motif"]
    # add a second spec that exhausts the gate and gets dropped (partial success)
    specs = specs + [
        {"layer_id": motif_layers[1]["id"], "subject": "octopus", "scope": "partial"}
    ]
    set_default_client(_ScriptedLLM(_wrapped(intent, specs)))
    recraft_adapter.set_default_recraft_client(_ScriptedRecraft(_GOOD_SVG, _BAD_SVG))
    resp = client.post("/api/v1/generate", json={"prompt": "x", "seed": 0})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidates"]
    assert any("dropped" in w for w in body["warnings"])


def test_route_prompt_all_motifs_fail_returns_502():
    intent, specs = _miss_intent_and_specs()
    set_default_client(_ScriptedLLM(_wrapped(intent, specs)))
    recraft_adapter.set_default_recraft_client(_ScriptedRecraft(_BAD_SVG, _BAD_SVG))
    resp = client.post("/api/v1/generate", json={"prompt": "x", "seed": 0})
    assert resp.status_code == 502, resp.text
