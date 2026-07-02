"""Multi-image chat binding: prompt + N images -> multimodal LLM role binding ->
vectorize motif images -> deterministic engine. All external calls scripted (no network,
no DB). Covers the RecraftHTTPClient.vectorize HTTP contract, vectorize_via_recraft (gate
+ image-hash freeze + register), the resolver source_image_index path (success + drop on
failure), build_intents image threading + index validation, and the /generate branch.
"""

import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.adapters.embedding as emb_adapter
import app.adapters.llm as llm_adapter
import app.adapters.recraft as recraft_adapter
import app.motifs.store as store_mod
from app.adapters.image import vectorize_limit_error
from app.adapters.llm import build_intents, set_default_client
from app.adapters.motif_resolver import resolve_motifs
from app.adapters.recraft import (
    RecraftError,
    RecraftHTTPClient,
    vectorize_via_recraft,
)
from app.main import app
from app.motifs.registry import MOTIFS, get_motif
from app.validate.intent import IntentInvalid
from tests._fakes import _ScriptedLLM
from tests.test_intent import mvp_intent

client = TestClient(app)

_MOTIF_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<path d="M2 2H8V8H2Z" fill="#3366cc"/></svg>'
)
_RASTER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<image href="data:image/png;base64,xx" width="10" height="10"/></svg>'
)


def _png(size: int = 300, color: tuple = (200, 60, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


class _FakeVectorizer:
    """RecraftClient exposing the vectorize seam; records the bytes it was handed."""

    def __init__(self, svg: str = _MOTIF_SVG) -> None:
        self._svg = svg
        self.calls: list[bytes] = []

    def vectorize(self, image_bytes: bytes) -> str:
        self.calls.append(image_bytes)
        return self._svg


# --- RecraftHTTPClient.vectorize HTTP contract -------------------------------


def test_vectorize_posts_multipart_then_fetches_svg():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["path"] = request.url.path
            seen["auth"] = request.headers.get("authorization")
            seen["ctype"] = request.headers.get("content-type", "")
            return httpx.Response(
                200, json={"image": {"url": "https://files.recraft.test/x.svg"}}
            )
        return httpx.Response(200, text=_MOTIF_SVG)  # follow-up GET for the .svg

    c = RecraftHTTPClient("tok", transport=httpx.MockTransport(handler))
    out = c.vectorize(_png())

    assert out == _MOTIF_SVG
    assert seen["path"].endswith("/images/vectorize")
    assert seen["auth"] == "Bearer tok"
    assert seen["ctype"].startswith("multipart/form-data")


def test_vectorize_http_error_maps_to_recraft_error():
    c = RecraftHTTPClient(
        "tok", transport=httpx.MockTransport(lambda r: httpx.Response(400, text="bad"))
    )
    with pytest.raises(RecraftError):
        c.vectorize(_png())


# --- vectorize_via_recraft: gate + register + image-hash freeze --------------


def test_vectorize_via_recraft_registers_and_freezes_by_image_hash():
    fake = _FakeVectorizer()
    img = _png()
    spec = {"layer_id": "logo", "subject": "logo", "scope": "whole"}

    mid = vectorize_via_recraft(img, spec, client=fake)
    assert get_motif(mid).id == mid
    assert len(fake.calls) == 1

    # Same bytes -> cache hit, no second client call (vectorize once per unique image).
    mid2 = vectorize_via_recraft(img, spec, client=fake)
    assert mid2 == mid
    assert len(fake.calls) == 1


def test_vectorize_via_recraft_screens_undersize_image_before_call():
    fake = _FakeVectorizer()
    with pytest.raises(RecraftError):
        vectorize_via_recraft(
            _png(size=100),  # below Recraft's 256px min side
            {"layer_id": "l", "subject": "x", "scope": "whole"},
            client=fake,
        )
    assert fake.calls == []  # screened before any API call


def test_vectorize_via_recraft_raster_result_raises():
    fake = _FakeVectorizer(svg=_RASTER_SVG)
    with pytest.raises(RecraftError):
        vectorize_via_recraft(
            _png(), {"layer_id": "l", "subject": "x", "scope": "whole"}, client=fake
        )


def test_vectorize_limit_error_accepts_in_range_png():
    assert vectorize_limit_error(_png(size=512)) is None
    assert vectorize_limit_error(_png(size=100)) is not None


# --- resolver: source_image_index routing ------------------------------------


def _motif_intent(*layer_ids: str) -> dict:
    return {
        "layers": [
            {"id": lid, "type": "motif", "params": {"motif_id": "ph"}}
            for lid in layer_ids
        ]
    }


def test_resolver_source_image_index_vectorizes_uploaded_image():
    fake = _FakeVectorizer()
    spec = {
        "layer_id": "logo",
        "subject": "logo",
        "scope": "whole",
        "source_image_index": 0,
    }
    out = resolve_motifs(
        _motif_intent("logo"), [spec], recraft_client=fake, images=[_png()]
    )
    mid = out["layers"][0]["params"]["motif_id"]
    assert mid != "ph" and get_motif(mid).id == mid
    assert len(fake.calls) == 1


def test_resolver_drops_failed_vectorize_keeps_survivor_with_warning():
    good, bad = _png(color=(10, 20, 30)), _png(color=(40, 50, 60))

    class _MixedVec:
        def __init__(self) -> None:
            self.seen: list[bytes] = []

        def vectorize(self, image_bytes: bytes) -> str:
            self.seen.append(image_bytes)
            return _MOTIF_SVG if image_bytes == good else _RASTER_SVG

    specs = [
        {"layer_id": "ok", "subject": "a", "scope": "whole", "source_image_index": 0},
        {"layer_id": "bad", "subject": "b", "scope": "whole", "source_image_index": 1},
    ]
    warnings: list[str] = []
    out = resolve_motifs(
        _motif_intent("ok", "bad"),
        specs,
        recraft_client=_MixedVec(),
        images=[good, bad],
        warnings=warnings,
    )
    ids = [layer["id"] for layer in out["layers"]]
    assert "ok" in ids and "bad" not in ids
    assert any("could not be vectorized" in w for w in warnings)


def test_resolver_invalid_source_image_index_drops_without_indexing():
    # Out-of-range index reaching the resolver must drop the layer, never subscript.
    fake = _FakeVectorizer()
    specs = [
        {"layer_id": "ok", "subject": "a", "scope": "whole", "source_image_index": 0},
        {"layer_id": "oob", "subject": "b", "scope": "whole", "source_image_index": 9},
    ]
    warnings: list[str] = []
    out = resolve_motifs(
        _motif_intent("ok", "oob"),
        specs,
        recraft_client=fake,
        images=[_png()],
        warnings=warnings,
    )
    ids = [layer["id"] for layer in out["layers"]]
    assert ids == ["ok"]
    assert len(fake.calls) == 1  # only the valid index 0 reached vectorize; index 9 never indexed


# --- build_intents: image threading + index validation -----------------------


def test_build_intents_threads_images_and_injects_binding_block():
    llm = _ScriptedLLM(json.dumps(mvp_intent()))
    img = _png()
    build_intents("swap the logo", client=llm, images=[img], use_cache=False)

    assert llm.image_calls == [[img]]  # bytes threaded to the multimodal seam
    assert "source_image_index" in llm.calls[0]  # role-binding block present


def test_build_intents_passes_valid_source_image_index_through():
    design = {
        "intent": mvp_intent(),
        "motif_specs": [
            {
                "layer_id": "circle_on_stripe",
                "subject": "logo",
                "scope": "whole",
                "source_image_index": 0,
            }
        ],
    }
    llm = _ScriptedLLM(json.dumps(design))
    res = build_intents("x", client=llm, images=[_png()], use_cache=False)[0]
    assert res.motif_specs[0]["source_image_index"] == 0


def test_build_intents_rejects_out_of_range_source_image_index():
    design = {
        "intent": mvp_intent(),
        "motif_specs": [
            {
                "layer_id": "circle_on_stripe",
                "subject": "logo",
                "scope": "whole",
                "source_image_index": 5,  # only one image provided
            }
        ],
    }
    llm = _ScriptedLLM(json.dumps(design), json.dumps(design))
    with pytest.raises(IntentInvalid):
        build_intents("x", client=llm, images=[_png()], use_cache=False)
    assert len(llm.calls) == 2  # invalid index -> one re-prompt, then give up


# --- /generate route: images + prompt branch ---------------------------------


def test_generate_images_path_vectorizes_and_returns_candidates():
    design = {
        "designs": [
            {
                "intent": mvp_intent(),
                "motif_specs": [
                    {
                        "layer_id": "circle_on_stripe",
                        "subject": "logo",
                        "scope": "whole",
                        "source_image_index": 0,
                    }
                ],
            }
        ]
    }
    set_default_client(_ScriptedLLM(json.dumps(design)))
    recraft_adapter.set_default_recraft_client(_FakeVectorizer())

    data_uri = "data:image/png;base64," + base64.b64encode(_png()).decode()
    resp = client.post(
        "/api/v1/generate",
        json={"prompt": "use this as the logo", "images": [data_uri]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates"]


def test_generate_images_path_rejects_bad_image_with_422():
    set_default_client(_ScriptedLLM(json.dumps({"designs": [{"intent": mvp_intent()}]})))
    resp = client.post(
        "/api/v1/generate",
        json={"prompt": "x", "images": ["not-valid-base64-!!!"]},
    )
    assert resp.status_code == 422


def test_session_generate_images_threads_cleaned_bytes():
    llm = _ScriptedLLM(json.dumps({"designs": [{"intent": mvp_intent()}]}))
    set_default_client(llm)
    data_uri = "data:image/png;base64," + base64.b64encode(_png()).decode()
    resp = client.post(
        "/api/v1/generate",
        json={"session_id": "session-images", "prompt": "use this", "images": [data_uri]},
    )
    assert resp.status_code == 200, resp.text
    assert llm.image_calls[0] is not None
    assert llm.image_calls[0][0].startswith(b"\x89PNG")


def test_session_generate_reference_image_threads_cleaned_bytes():
    llm = _ScriptedLLM(json.dumps({"designs": [{"intent": mvp_intent()}]}))
    set_default_client(llm)
    image_b64 = base64.b64encode(_png()).decode()
    resp = client.post(
        "/api/v1/generate",
        json={
            "session_id": "session-reference-image",
            "prompt": "use this",
            "reference_image": image_b64,
        },
    )
    assert resp.status_code == 200, resp.text
    assert llm.image_calls[0] is not None
    assert llm.image_calls[0][0].startswith(b"\x89PNG")
