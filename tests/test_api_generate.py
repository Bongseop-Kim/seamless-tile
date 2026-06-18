import asyncio

import httpx
from fastapi.testclient import TestClient

from app.main import app
from tests.test_intent import mvp_intent

client = TestClient(app)


def test_generate_returns_product_shape():
    resp = client.post(
        "/api/v1/generate", json={"intent": mvp_intent(), "candidate_count": 4}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"]
    assert len(body["candidates"]) == 4
    cand = body["candidates"][0]
    assert set(cand) == {"id", "svg", "intent", "layout_id", "source_fidelity", "repro"}
    assert cand["svg"].startswith("<svg")
    assert cand["source_fidelity"] == "vector"
    assert cand["repro"]["seed"] == 184231
    assert cand["repro"]["colorway_id"] == "default"
    assert cand["repro"]["layout_id"] == cand["layout_id"]


def test_candidates_are_diverse_and_deduped():
    resp = client.post(
        "/api/v1/generate", json={"intent": mvp_intent(), "candidate_count": 4}
    )
    body = resp.json()
    cands = body["candidates"]

    layouts = [c["layout_id"] for c in cands]
    svgs = [c["svg"] for c in cands]
    ids = [c["id"] for c in cands]
    # de-dup: every candidate is a distinct svg with a distinct id
    assert len(set(svgs)) == len(svgs)
    assert len(set(ids)) == len(ids)
    # diversity: distinct layout_id >= min(2, available strategies)
    assert len(set(layouts)) >= 2
    assert "diversity shortfall" not in " ".join(body["warnings"])


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


def test_determinism_same_request_same_candidates():
    payload = {"intent": mvp_intent(), "candidate_count": 4, "seed": 999}
    a = client.post("/api/v1/generate", json=payload).json()
    b = client.post("/api/v1/generate", json=payload).json()

    # request_id aside, the candidate set is byte-identical
    assert [c["svg"] for c in a["candidates"]] == [c["svg"] for c in b["candidates"]]
    assert [c["id"] for c in a["candidates"]] == [c["id"] for c in b["candidates"]]
    assert a["candidates"][0]["repro"]["seed"] == 999


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


def test_prompt_only_without_intent_returns_422():
    resp = client.post("/api/v1/generate", json={"prompt": "navy paisley tie"})
    assert resp.status_code == 422
    assert "session 7" in str(resp.json()["detail"])


def test_partial_success_when_count_exceeds_available():
    # The MVP intent only diversifies along symmetry (one colorway, no scatter), so
    # asking for more candidates than distinct layouts yields a partial result.
    resp = client.post(
        "/api/v1/generate", json={"intent": mvp_intent(), "candidate_count": 8}
    )
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["candidates"]) < 8
    assert any("partial" in w for w in body["warnings"])


def test_deferred_fields_are_accepted_with_warning():
    resp = client.post(
        "/api/v1/generate",
        json={"intent": mvp_intent(), "prompt": "ignored for now"},
    )
    assert resp.status_code == 200
    assert any("prompt" in w for w in resp.json()["warnings"])
