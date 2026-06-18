from fastapi.testclient import TestClient

from app.main import app
from tests.test_intent import mvp_intent

client = TestClient(app)


def test_generate_route_returns_candidate():
    resp = client.post("/api/v1/generate", json={"intent": mvp_intent()})

    assert resp.status_code == 200
    body = resp.json()
    assert body["svg"].startswith("<svg")
    assert body["repro"]["seed"] == 184231
    assert body["repro"]["colorway_id"] == "default"
    assert body["warnings"] == []


def test_generate_route_maps_validation_errors_to_422():
    intent = mvp_intent()
    intent["layers"][0]["params"]["color"] = "missing"

    resp = client.post("/api/v1/generate", json={"intent": intent})

    assert resp.status_code == 422
    assert "missing" in str(resp.json()["detail"])
