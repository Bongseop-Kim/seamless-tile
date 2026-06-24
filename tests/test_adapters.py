"""Session-7 adapter tests. All external calls (LLM, VLM, vectorizer) are mocked —
no network. Palette extraction runs for real against synthetic Pillow images."""

import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, features

import app.api.routes.generate as gen_route
from app.adapters import image as image_adapter
from app.adapters import llm as llm_adapter
from app.adapters.base import AdapterResult
from app.adapters.image import (
    ImageAdapterError,
    VectorResult,
    build_intent as image_build_intent,
    extract_palette,
)
from app.adapters.image import judge_vectorization
from app.adapters.llm import (
    LLMNotConfigured,
    build_intent as llm_build_intent,
    build_intents as llm_build_intents,
)
from app.main import app
from app.validate.intent import IntentInvalid, validate_intent
from tests.test_intent import mvp_intent

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_caches():
    llm_adapter.clear_intent_cache()
    image_adapter.clear_intent_cache()
    yield
    llm_adapter.clear_intent_cache()
    image_adapter.clear_intent_cache()


# --- fakes -----------------------------------------------------------------


from tests._fakes import _ScriptedLLM


class _StubVLM:
    def __init__(self, motif_id: str | None) -> None:
        self._motif_id = motif_id

    def describe(self, image_bytes: bytes) -> dict:
        return {"motif_id": self._motif_id}


class _FakeVectorizer:
    def __init__(self, path_count: int, color_count: int) -> None:
        self._r = VectorResult(path_count=path_count, color_count=color_count)
        self.calls = 0

    def trace(self, image_bytes: bytes) -> VectorResult:
        self.calls += 1
        return self._r


class _UnhashableVLM:
    """A misbehaving VLM whose motif_id is unhashable (would crash a naive `in MOTIFS`)."""

    def describe(self, image_bytes: bytes) -> dict:
        return {"motif_id": ["not", "a", "string"]}


class _FailingVLM:
    def describe(self, image_bytes: bytes) -> dict:
        raise RuntimeError("vlm down")


class _FailingVectorizer:
    def trace(self, image_bytes: bytes) -> VectorResult:
        raise RuntimeError("vectorizer down")


def _png_b64(*colors: tuple[int, int, int]) -> str:
    """An 8x8 image split into vertical bands of the given RGB colors."""
    img = Image.new("RGB", (8, 8), colors[0])
    band = max(1, 8 // len(colors))
    for i, c in enumerate(colors):
        for x in range(i * band, min((i + 1) * band, 8)):
            for y in range(8):
                img.putpixel((x, y), c)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --- LLM adapter -----------------------------------------------------------


def test_llm_adapter_builds_valid_intent():
    llm = _ScriptedLLM(json.dumps(mvp_intent()))
    res = llm_build_intent("navy diagonal club tie", client=llm, use_cache=False)
    assert isinstance(res, AdapterResult)
    assert res.source_fidelity == "vector"
    assert res.intent["intent_version"] == 1
    # the frozen intent is itself valid (engine will re-validate, idempotently)
    validate_intent(res.intent)
    assert len(llm.calls) == 1


def test_build_intents_returns_multiple_designs():
    d1 = mvp_intent()
    d2 = mvp_intent()
    d2["seed"] = 999  # a distinct (still valid) design
    payload = json.dumps({"designs": [{"intent": d1}, {"intent": d2}]})
    llm = _ScriptedLLM(payload)
    results = llm_build_intents("striped tie", client=llm, use_cache=False)
    assert len(results) == 2
    assert all(isinstance(r, AdapterResult) for r in results)
    assert len(llm.calls) == 1  # both valid on the first attempt


def test_build_intents_drops_invalid_design_without_reprompt():
    good = {"intent": mvp_intent()}
    bad = {"intent": {"intent_version": 1}}  # missing canvas/palette/...
    llm = _ScriptedLLM(json.dumps({"designs": [bad, good]}))
    results = llm_build_intents("x", client=llm, use_cache=False)
    assert len(results) == 1  # the invalid design is dropped
    assert len(llm.calls) == 1  # >=1 valid -> no re-prompt
    validate_intent(results[0].intent)


def test_build_intents_all_invalid_reprompts_then_raises():
    bad = json.dumps({"designs": [{"intent": {"intent_version": 1}}]})
    llm = _ScriptedLLM(bad, bad)
    with pytest.raises(IntentInvalid):
        llm_build_intents("x", client=llm, use_cache=False)
    assert len(llm.calls) == 2  # one re-prompt when zero designs valid


def test_build_intents_accepts_legacy_single_wrapper():
    llm = _ScriptedLLM(json.dumps({"intent": mvp_intent()}))
    results = llm_build_intents("x", client=llm, use_cache=False)
    assert len(results) == 1


def test_build_intent_wrapper_returns_first_design():
    d1 = mvp_intent()
    d2 = mvp_intent()
    d2["seed"] = 5
    llm = _ScriptedLLM(json.dumps({"designs": [{"intent": d1}, {"intent": d2}]}))
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert isinstance(res, AdapterResult)
    assert res.intent["seed"] == d1["seed"]


def test_normalize_stripes_forces_diagonal_to_visual_up_right():
    import math

    from app.adapters.llm import _normalize_stripes
    from app.core.config import get_settings

    raw = mvp_intent()
    next(layer for layer in raw["layers"] if layer["type"] == "stripe")["params"][
        "angle"
    ] = -36.87
    _normalize_stripes(raw, get_settings())
    st = next(layer for layer in raw["layers"] if layer["type"] == "stripe")["params"]
    assert st["angle"] == -45.0
    assert abs(st["period_mm"] - 48 / math.sqrt(2)) < 1e-3  # k=1 -> 2 stripes/tile
    validate_intent(raw)  # still valid + seamless


def test_normalize_stripes_preserves_axis():
    from app.adapters.llm import _normalize_stripes
    from app.core.config import get_settings

    raw = mvp_intent()
    next(layer for layer in raw["layers"] if layer["type"] == "stripe")["params"][
        "angle"
    ] = 90.0
    _normalize_stripes(raw, get_settings())
    st = next(layer for layer in raw["layers"] if layer["type"] == "stripe")["params"]
    assert st["angle"] == 90.0  # vertical/horizontal stripes untouched


def test_normalize_stripes_repeats_controls_k():
    import math

    from app.adapters.llm import _normalize_stripes

    raw = mvp_intent()

    class _Settings:
        stripe_diagonal_repeats = 4

    _normalize_stripes(raw, _Settings())
    st = next(layer for layer in raw["layers"] if layer["type"] == "stripe")["params"]
    assert abs(st["period_mm"] - 48 / (2 * math.sqrt(2))) < 1e-3  # k=2 -> 4 stripes/tile


def test_build_intents_normalizes_diagonal_stripe():
    llm = _ScriptedLLM(json.dumps(mvp_intent()))
    res = llm_build_intents("diagonal repp stripe tie", client=llm, use_cache=False)
    st = next(layer for layer in res[0].intent["layers"] if layer["type"] == "stripe")["params"]
    assert st["angle"] == -45.0


def test_llm_prompt_does_not_make_stripes_the_default():
    llm = _ScriptedLLM(json.dumps(mvp_intent()))
    llm_build_intent(
        "simple circular polka dots on a solid background",
        client=llm,
        use_cache=False,
    )

    prompt = llm.calls[0]
    assert "diagonal stripes are the default" not in prompt
    assert "polka dots" in prompt
    assert "lattice placement" in prompt
    assert "Placement specs are mandatory" in prompt
    assert "do NOT add stripe host layers" in prompt


def test_llm_adapter_reprompts_once_then_succeeds():
    bad = json.dumps({"intent_version": 1})  # missing canvas/palette/... -> invalid
    good = json.dumps(mvp_intent())
    llm = _ScriptedLLM(bad, good)
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert len(llm.calls) == 2
    # the re-prompt carried the validation errors back to the model
    assert "FAILED stage-0 validation" in llm.calls[1]
    assert res.intent["canvas"]["tile_mm"] == 48


def test_llm_adapter_raises_after_failed_reprompt():
    bad = json.dumps({"intent_version": 1})
    llm = _ScriptedLLM(bad, bad)
    with pytest.raises(IntentInvalid):
        llm_build_intent("x", client=llm, use_cache=False)
    assert len(llm.calls) == 2  # exactly one re-prompt, no more


def test_llm_adapter_handles_non_json():
    llm = _ScriptedLLM("not json at all", json.dumps(mvp_intent()))
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert len(llm.calls) == 2
    assert res.intent["intent_version"] == 1


def test_llm_adapter_strips_single_line_code_fence():
    llm = _ScriptedLLM(f"```json {json.dumps(mvp_intent())}```")
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert res.intent["intent_version"] == 1


def test_llm_adapter_rejects_non_string_optional_spec_facets():
    intent = mvp_intent()
    bad = {
        "intent": intent,
        "motif_specs": [
            {
                "layer_id": "circle_on_stripe",
                "subject": "pig",
                "scope": "partial",
                "view": 7,
            }
        ],
    }
    good = {
        "intent": intent,
        "motif_specs": [
            {
                "layer_id": "circle_on_stripe",
                "subject": "pig",
                "scope": "partial",
                "view": "front",
            }
        ],
    }
    llm = _ScriptedLLM(json.dumps(bad), json.dumps(good))
    res = llm_build_intent("x", client=llm, use_cache=False)
    assert len(llm.calls) == 2
    assert res.motif_specs[0]["view"] == "front"


def test_llm_adapter_drops_redundant_builtin_specs():
    intent = mvp_intent()
    specs = [
        {"layer_id": "circle_on_stripe", "subject": "circle", "scope": "whole"},
        {"layer_id": "bee_on_stripe", "subject": "pig", "scope": "whole"},
    ]
    llm = _ScriptedLLM(json.dumps({"intent": intent, "motif_specs": specs}))

    res = llm_build_intent("x", client=llm, use_cache=False)

    assert res.motif_specs == [specs[1]]


def test_llm_adapter_caches_frozen_intent():
    first, second = mvp_intent(), mvp_intent()
    second["seed"] = 424242  # a distinct response, so equality below is non-vacuous
    llm = _ScriptedLLM(json.dumps(first), json.dumps(second))
    a = llm_build_intent("a stable unique prompt", client=llm)
    b = llm_build_intent("a stable unique prompt", client=llm)
    assert len(llm.calls) == 1  # second call served from the freeze cache
    assert a.intent == b.intent  # the FROZEN first result was replayed...
    assert b.intent["seed"] == first["seed"]  # ...not the second scripted response


def test_llm_cache_hit_replays_warnings():
    intent = mvp_intent()
    intent["canvas"]["dpi"] = 200  # not in {150,300,600} -> stage-0 clamp warning
    llm = _ScriptedLLM(json.dumps(intent))
    a = llm_build_intent("a warning-triggering prompt", client=llm)
    b = llm_build_intent("a warning-triggering prompt", client=llm)
    assert a.warnings  # the dpi clamp warning is surfaced
    assert a.warnings == b.warnings  # and replayed identically on the cache hit
    assert len(llm.calls) == 1


def test_llm_cached_intent_is_isolated_from_caller_mutation():
    llm = _ScriptedLLM(json.dumps(mvp_intent()))
    a = llm_build_intent("isolation prompt", client=llm)
    a.intent["canvas"]["tile_mm"] = 999  # must not corrupt the freeze
    b = llm_build_intent("isolation prompt", client=llm)
    assert b.intent["canvas"]["tile_mm"] == 48


def test_llm_adapter_unconfigured_raises():
    with pytest.raises(LLMNotConfigured):
        llm_build_intent("x", use_cache=False)


# --- image adapter ---------------------------------------------------------


def test_extract_palette_is_deterministic_hex_slots():
    b64 = _png_b64((16, 36, 58))  # solid navy
    data = base64.b64decode(b64)
    slots = extract_palette(data, num_colors=4)
    assert slots[0]["hex"] == "#10243a"
    assert len(slots) >= 2  # padded so there is always ground + accent
    assert extract_palette(data, num_colors=4) == slots  # deterministic


def test_image_adapter_builds_valid_intent_from_palette():
    res = image_build_intent(_png_b64((16, 36, 58), (239, 138, 122)), use_cache=False)
    assert res.source_fidelity == "vector"  # no vectorizer injected -> default vector
    assert res.intent["palette"]["slots"]
    validate_intent(res.intent)  # frozen intent passes stage-0


def test_image_adapter_marks_unfit_texture_as_raster_hybrid():
    res = image_build_intent(
        _png_b64((16, 36, 58), (239, 138, 122)),
        vectorizer=_FakeVectorizer(path_count=5000, color_count=200),
        use_cache=False,
    )
    assert res.source_fidelity == "raster_hybrid"
    assert any("unfit" in w for w in res.warnings)


def test_image_adapter_marks_fit_texture_as_vector():
    res = image_build_intent(
        _png_b64((16, 36, 58), (239, 138, 122)),
        vectorizer=_FakeVectorizer(path_count=120, color_count=8),
        use_cache=False,
    )
    assert res.source_fidelity == "vector"


def test_image_adapter_honors_registry_motif_hint():
    res = image_build_intent(
        _png_b64((16, 36, 58), (239, 138, 122)),
        vlm=_StubVLM("bee"),
        use_cache=False,
    )
    motif_ids = [
        layer["params"]["motif_id"]
        for layer in res.intent["layers"]
        if layer["type"] == "motif"
    ]
    assert "bee" in motif_ids


def test_image_adapter_ignores_unknown_motif_hint():
    res = image_build_intent(
        _png_b64((16, 36, 58), (239, 138, 122)),
        vlm=_StubVLM("definitely-not-a-registered-motif"),
        use_cache=False,
    )
    motif_ids = [
        layer["params"]["motif_id"]
        for layer in res.intent["layers"]
        if layer["type"] == "motif"
    ]
    assert motif_ids == ["circle"]  # falls back to the always-present library motif


def test_image_adapter_maps_vlm_failure_to_adapter_error():
    with pytest.raises(ImageAdapterError, match="VLM service failed"):
        image_build_intent(
            _png_b64((16, 36, 58), (239, 138, 122)),
            vlm=_FailingVLM(),
            use_cache=False,
        )


def test_image_adapter_maps_vectorizer_failure_to_adapter_error():
    with pytest.raises(ImageAdapterError, match="vectorizer service failed"):
        image_build_intent(
            _png_b64((16, 36, 58), (239, 138, 122)),
            vectorizer=_FailingVectorizer(),
            use_cache=False,
        )


def test_image_adapter_rejects_bad_base64():
    with pytest.raises(IntentInvalid):
        image_build_intent("!!! not base64 !!!", use_cache=False)


def test_judge_vectorization_boundaries():
    assert judge_vectorization(VectorResult(1500, 32)) == "vector"
    assert judge_vectorization(VectorResult(1501, 32)) == "raster_hybrid"
    assert judge_vectorization(VectorResult(1500, 33)) == "raster_hybrid"


def test_image_adapter_caches_frozen_intent():
    b64 = _png_b64((16, 36, 58), (239, 138, 122))
    vec = _FakeVectorizer(path_count=5000, color_count=200)  # unfit -> raster_hybrid
    a = image_build_intent(b64, vectorizer=vec)  # caching ON (default)
    b = image_build_intent(b64, vectorizer=vec)
    assert vec.calls == 1  # second call served from cache; vectorizer not re-run
    assert a.intent == b.intent
    assert a.source_fidelity == b.source_fidelity == "raster_hybrid"
    assert a.warnings == b.warnings  # fidelity + warnings replayed on the hit


def test_image_cached_intent_is_isolated_from_caller_mutation():
    b64 = _png_b64((16, 36, 58), (239, 138, 122))
    a = image_build_intent(b64)
    a.intent["canvas"]["tile_mm"] = 999  # must not corrupt the freeze
    b = image_build_intent(b64)
    assert b.intent["canvas"]["tile_mm"] == 48.0


def test_image_adapter_drops_motif_on_constrained_retry(monkeypatch):
    real = image_adapter.validate_intent
    state = {"n": 0}

    def flaky(raw, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise IntentInvalid(["forced first-attempt failure"])  # triggers the retry
        return real(raw, **kwargs)

    monkeypatch.setattr(image_adapter, "validate_intent", flaky)
    res = image_build_intent(_png_b64((16, 36, 58), (239, 138, 122)), use_cache=False)
    assert state["n"] == 2  # initial attempt + one constrained retry
    assert [l for l in res.intent["layers"] if l["type"] == "motif"] == []  # motif dropped


def test_image_adapter_retry_exhausted_raises_intent_invalid(monkeypatch):
    def always_fail(raw, **kwargs):
        raise IntentInvalid(["always invalid"])

    monkeypatch.setattr(image_adapter, "validate_intent", always_fail)
    with pytest.raises(IntentInvalid):
        image_build_intent(_png_b64((16, 36, 58), (239, 138, 122)), use_cache=False)


def test_image_adapter_ignores_unhashable_motif_hint():
    res = image_build_intent(
        _png_b64((16, 36, 58), (239, 138, 122)), vlm=_UnhashableVLM(), use_cache=False
    )
    motif_ids = [
        layer["params"]["motif_id"]
        for layer in res.intent["layers"]
        if layer["type"] == "motif"
    ]
    assert motif_ids == ["circle"]  # malformed hint ignored, no crash


def test_image_adapter_rejects_data_uri_without_payload():
    with pytest.raises(IntentInvalid):
        image_build_intent("data:image/png;base64", use_cache=False)  # no comma


# --- route wiring (monkeypatch the route's adapter bindings) ----------------


def test_route_prompt_path_returns_candidates(monkeypatch):
    def fake(prompt, **kwargs):
        return [AdapterResult(intent=mvp_intent(), source_fidelity="vector", warnings=[])]

    monkeypatch.setattr(gen_route, "llm_build_intents", fake)
    resp = client.post("/api/v1/generate", json={"prompt": "navy club tie"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidates"]
    assert set(body["candidates"][0]) == {"id", "png_url"}


def test_route_image_path_threads_source_fidelity(monkeypatch):
    def fake(image_b64, **kwargs):
        return AdapterResult(
            intent=mvp_intent(), source_fidelity="raster_hybrid", warnings=["texture unfit"]
        )

    monkeypatch.setattr(gen_route, "image_build_intent", fake)
    # source_fidelity is no longer in the response; it is threaded into the generation
    # log row instead. Capture the row to assert the threading still holds.
    captured: list = []
    monkeypatch.setattr(
        gen_route, "insert_generation_log", lambda row: captured.append(row)
    )
    resp = client.post("/api/v1/generate", json={"reference_image": "ZmFrZQ=="})
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidates"]
    assert "texture unfit" in body["warnings"]
    assert captured, "expected a generation log row"
    assert all(c["source_fidelity"] == "raster_hybrid" for c in captured[0].candidates)


def test_route_adapter_invalid_returns_422(monkeypatch):
    def fake(prompt, **kwargs):
        raise IntentInvalid(["bad intent after re-prompt"])

    monkeypatch.setattr(gen_route, "llm_build_intents", fake)
    resp = client.post("/api/v1/generate", json={"prompt": "x"})
    assert resp.status_code == 422


def test_route_adapter_client_failure_returns_502(monkeypatch):
    def fake(prompt, **kwargs):
        raise LLMNotConfigured("no client")

    monkeypatch.setattr(gen_route, "llm_build_intents", fake)
    resp = client.post("/api/v1/generate", json={"prompt": "x"})
    assert resp.status_code == 502


def test_route_image_path_bad_base64_returns_422():
    # Real image adapter (no monkeypatch): IntentInvalid must reach the route as 422.
    resp = client.post("/api/v1/generate", json={"reference_image": "!!! not base64 !!!"})
    assert resp.status_code == 422


def test_route_image_path_malformed_data_uri_returns_422():
    # A comma-less data URI used to raise an unhandled IndexError (500).
    resp = client.post("/api/v1/generate", json={"reference_image": "data:image/png;base64"})
    assert resp.status_code == 422


def test_route_requires_some_input():
    resp = client.post("/api/v1/generate", json={})
    assert resp.status_code == 422
    assert "required" in str(resp.json()["detail"]).lower()


def test_route_intent_direct_warns_ignored_fields():
    resp = client.post(
        "/api/v1/generate", json={"intent": mvp_intent(), "prompt": "ignored"}
    )
    assert resp.status_code == 200
    assert any("ignored because `intent`" in w for w in resp.json()["warnings"])


# --- session-8 upload validation -------------------------------------------


def _img_b64(fmt: str, size: tuple[int, int] = (8, 8), color: tuple[int, int, int] = (16, 36, 58)) -> str:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_upload_rejects_disallowed_format():
    with pytest.raises(IntentInvalid):
        image_build_intent(_img_b64("GIF"), use_cache=False)


def test_upload_rejects_format_spoof_polyglot():
    # Valid base64, but the bytes are HTML, not an image (sniffs to no allowed format).
    payload = base64.b64encode(b"<html><script>alert(1)</script></html>").decode("ascii")
    with pytest.raises(IntentInvalid):
        image_build_intent(payload, use_cache=False)


def test_upload_allows_jpeg():
    res = image_build_intent(_img_b64("JPEG", color=(16, 36, 58)), use_cache=False)
    assert res.intent["palette"]["slots"]


def test_validate_image_rejects_oversize_dimension(monkeypatch):
    monkeypatch.setattr(image_adapter, "MAX_IMAGE_DIM", 4)  # 8x8 image exceeds it
    data = base64.b64decode(_png_b64((16, 36, 58)))
    with pytest.raises(IntentInvalid):
        image_adapter._validate_image(data)


def test_validate_image_rejects_pixel_bomb(monkeypatch):
    monkeypatch.setattr(image_adapter, "MAX_IMAGE_PIXELS", 10)  # 8*8=64 exceeds it
    data = base64.b64decode(_png_b64((16, 36, 58)))
    with pytest.raises(IntentInvalid):
        image_adapter._validate_image(data)


def test_strip_metadata_drops_text_and_icc():
    from PIL import PngImagePlugin

    img = Image.new("RGB", (8, 8), (16, 36, 58))
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Secret", "leak-me")
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta, icc_profile=b"FAKEICCPROFILE")
    raw = buf.getvalue()
    assert b"leak-me" in raw  # metadata present before the strip

    clean = image_adapter._strip_metadata(raw)
    assert b"leak-me" not in clean
    reopened = Image.open(io.BytesIO(clean))
    assert "icc_profile" not in reopened.info
    assert reopened.size == (8, 8)  # pixels preserved


def test_route_rejects_disallowed_format_422():
    resp = client.post("/api/v1/generate", json={"reference_image": _img_b64("GIF")})
    assert resp.status_code == 422


def test_upload_allows_webp():
    if not features.check("webp"):
        pytest.skip("WEBP encoding unavailable in this Pillow build")
    b64 = _img_b64("WEBP")
    res = image_build_intent(b64, use_cache=False)
    assert res.intent["palette"]["slots"]


def test_validate_image_rejects_truncated_stream():
    data = base64.b64decode(_png_b64((16, 36, 58)))
    # Header (size) is intact but the stream is cut, so verify() must reject it.
    with pytest.raises(IntentInvalid):
        image_adapter._validate_image(data[:40])


def test_strip_metadata_preserves_pixels():
    img = Image.new("RGB", (4, 4))
    for i in range(16):
        img.putpixel((i % 4, i // 4), ((i * 17) % 256, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    clean = image_adapter._strip_metadata(buf.getvalue())
    out = Image.open(io.BytesIO(clean)).convert("RGB")
    assert out.tobytes() == img.tobytes()  # lossless: pixels intact
