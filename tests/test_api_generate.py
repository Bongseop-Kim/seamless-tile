import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_intent import mvp_intent

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_generate_cache():
    """The response cache is a module-level dict that lives for the whole pytest process;
    clear it around every test so identical payloads across tests don't cross-pollinate."""
    from app.api.routes.generate import reset_response_cache

    reset_response_cache()
    yield
    reset_response_cache()


@pytest.fixture
def fake_preview(monkeypatch):
    """Pretend preview storage is configured and return a deterministic fake URL per
    object path, so API tests exercise the configured path without an SVG renderer or
    network. (SVG/PNG determinism itself is covered by the engine-level tests.)"""
    import app.api.routes.generate as route

    monkeypatch.setattr(route, "preview_configured", lambda: True)
    monkeypatch.setattr(
        route,
        "make_preview",
        lambda svg, *, tile_mm, dpi, path: f"https://preview.test/{path}",
    )


def test_generate_returns_product_shape(fake_preview):
    resp = client.post(
        "/api/v1/generate", json={"intent": mvp_intent(), "candidate_count": 4}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"]
    assert len(body["candidates"]) == 4
    cand = body["candidates"][0]
    # slimmed contract: only id + png_url (svg/intent/repro are server-side logged)
    assert set(cand) == {"id", "png_url"}
    assert cand["png_url"].startswith("https://preview.test/")
    assert cand["png_url"].endswith(f"/{cand['id']}.png")


def test_candidates_are_diverse_and_deduped(fake_preview):
    resp = client.post(
        "/api/v1/generate", json={"intent": mvp_intent(), "candidate_count": 4}
    )
    body = resp.json()
    cands = body["candidates"]

    ids = [c["id"] for c in cands]
    png_urls = [c["png_url"] for c in cands]
    # de-dup: every candidate is a distinct id (=> distinct preview object path)
    assert len(set(ids)) == len(ids)
    assert len(set(png_urls)) == len(png_urls)
    assert "diversity shortfall" not in " ".join(body["warnings"])


def test_intent_level_warnings_are_deduped(fake_preview):
    # An out-of-gamut color emits a per-candidate intent warning; across candidates the
    # identical message must collapse to one (regression guard for warning dedup).
    intent = mvp_intent()
    intent["palette"]["slots"][2]["hex"] = "#ffd700"
    intent["colorways"][0]["mapping"]["gold"] = "#ffd700"
    resp = client.post(
        "/api/v1/generate", json={"intent": intent, "candidate_count": 4}
    )
    assert resp.status_code == 200
    w = resp.json()["warnings"]
    assert len(w) == len(set(w))  # no exact duplicates
    assert sum("outside CMYK gamut" in m for m in w) == 1


def test_preview_storage_unconfigured_yields_null_url():
    # Unconfigured preview upload is a graceful no-op.
    resp = client.post("/api/v1/generate", json={"intent": mvp_intent()})
    body = resp.json()
    assert resp.status_code == 200
    assert body["candidates"]
    assert all(c["png_url"] is None for c in body["candidates"])
    assert any("preview storage not configured" in w for w in body["warnings"])


def test_request_id_propagates_to_body_and_header():
    resp = client.post("/api/v1/generate", json={"intent": mvp_intent()})
    assert resp.json()["request_id"] == resp.headers["X-Request-ID"]


def test_request_id_echoed_from_header():
    resp = client.post(
        "/api/v1/generate",
        headers={"X-Request-ID": "trace-xyz"},
        json={"intent": mvp_intent()},
    )
    assert resp.json()["request_id"] == "trace-xyz"
    assert resp.headers["X-Request-ID"] == "trace-xyz"


def test_request_id_header_is_sanitized():
    resp = client.post(
        "/api/v1/generate",
        headers={"X-Request-ID": "bad id.with spaces"},
        json={"intent": mvp_intent()},
    )
    assert resp.json()["request_id"] == "bad-id-with-spaces"
    assert resp.headers["X-Request-ID"] == "bad-id-with-spaces"


def test_determinism_same_request_same_candidates(fake_preview):
    payload = {"intent": mvp_intent(), "candidate_count": 4, "seed": 999}
    a = client.post("/api/v1/generate", json=payload).json()
    b = client.post("/api/v1/generate", json=payload).json()

    # request_id aside, the candidate id set is byte-identical (engine determinism;
    # the byte-identical SVG itself is asserted in tests/test_determinism.py)
    assert [c["id"] for c in a["candidates"]] == [c["id"] for c in b["candidates"]]


def test_error_response_body_includes_request_id():
    intent = mvp_intent()
    intent["layers"][0]["params"]["color"] = "missing"
    resp = client.post(
        "/api/v1/generate", headers={"X-Request-ID": "err-1"}, json={"intent": intent}
    )
    assert resp.status_code == 422
    assert resp.json()["request_id"] == "err-1"
    assert resp.headers["X-Request-ID"] == "err-1"


def test_concurrent_requests_keep_distinct_request_ids():
    # Exercises the contextvar + middleware path under real concurrency: each response
    # must echo its own injected id with no cross-talk.
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:

            async def one(i: int):
                rid = f"req-{i}"
                r = await ac.post(
                    "/api/v1/generate",
                    headers={"X-Request-ID": rid},
                    json={"intent": mvp_intent(), "candidate_count": 2},
                )
                return rid, r.json()["request_id"], r.headers["X-Request-ID"]

            return await asyncio.gather(*[one(i) for i in range(20)])

    for sent, body_rid, header_rid in asyncio.run(run()):
        assert body_rid == sent == header_rid


def test_semantic_invalid_intent_returns_422():
    intent = mvp_intent()
    intent["layers"][0]["params"]["color"] = "missing"

    resp = client.post("/api/v1/generate", json={"intent": intent})

    assert resp.status_code == 422
    assert "missing" in str(resp.json()["detail"])


def test_schema_invalid_request_returns_400():
    # candidate_count out of the schema range is a request-schema failure, not semantic
    resp = client.post(
        "/api/v1/generate", json={"intent": mvp_intent(), "candidate_count": 99}
    )
    assert resp.status_code == 400


def test_prompt_only_without_client_returns_502():
    # Session 7 wires the LLM adapter, but no client is configured by default, so a
    # prompt-only request surfaces as a 5xx (external dependency unavailable), not 422.
    resp = client.post("/api/v1/generate", json={"prompt": "navy paisley tie"})
    assert resp.status_code == 502
    assert "client" in str(resp.json()["detail"]).lower()


def test_partial_success_when_count_exceeds_available(fake_preview):
    # Asking for more candidates than distinct deterministic variants yields partial.
    intent = mvp_intent()
    intent["layers"] = [intent["layers"][0]]
    resp = client.post(
        "/api/v1/generate", json={"intent": intent, "candidate_count": 8}
    )
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["candidates"]) < 8
    assert any("partial" in w for w in body["warnings"])


def test_deferred_fields_are_accepted_with_warning(fake_preview):
    resp = client.post(
        "/api/v1/generate",
        json={"intent": mvp_intent(), "prompt": "ignored for now"},
    )
    assert resp.status_code == 200
    assert any("prompt" in w for w in resp.json()["warnings"])


def test_identical_request_served_from_cache(fake_preview, monkeypatch):
    """A second identical request skips the engine/render entirely and replays the cached
    candidates + preview URLs, but still carries its own fresh request_id."""
    import app.api.routes.generate as route

    n = {"c": 0}
    real = route.generate_candidates

    def counting_generate(*a, **k):
        n["c"] += 1
        return real(*a, **k)

    monkeypatch.setattr(route, "generate_candidates", counting_generate)
    payload = {"intent": mvp_intent(), "candidate_count": 4, "seed": 999}
    a = client.post("/api/v1/generate", json=payload).json()
    b = client.post(
        "/api/v1/generate", headers={"X-Request-ID": "second"}, json=payload
    ).json()

    assert n["c"] == 1  # second request never reached the engine
    assert [c["id"] for c in a["candidates"]] == [c["id"] for c in b["candidates"]]
    assert [c["png_url"] for c in a["candidates"]] == [
        c["png_url"] for c in b["candidates"]
    ]
    assert b["request_id"] == "second"  # fresh request_id, not the cached original


def test_cache_disabled_when_size_zero(fake_preview, monkeypatch):
    """generate_cache_size=0 disables lookup+store: every request re-runs the engine."""
    import app.api.routes.generate as route
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generate_cache_size", 0)
    n = {"c": 0}
    real = route.generate_candidates
    monkeypatch.setattr(
        route,
        "generate_candidates",
        lambda *a, **k: (n.__setitem__("c", n["c"] + 1) or real(*a, **k)),
    )
    payload = {"intent": mvp_intent(), "candidate_count": 2, "seed": 7}
    client.post("/api/v1/generate", json=payload)
    client.post("/api/v1/generate", json=payload)
    assert n["c"] == 2
