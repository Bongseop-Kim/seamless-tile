import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.domain.colorway import PALETTES
from app.main import app

client = TestClient(app)

STRIPE = {"widths_mm": [10, 10], "colors": ["#ffffff", "#00aa33"], "tile_mm": 20}


def _create_stripe() -> str:
    resp = client.post("/api/v1/patterns/stripe", json=STRIPE)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _fills(svg: str) -> set[str]:
    return {el.get("fill") for el in ET.fromstring(svg).iter() if el.get("fill")}


def test_recolor_changes_colors_keeps_geometry():
    pid = _create_stripe()
    original = client.get(f"/api/v1/patterns/{pid}").text

    resp = client.post(
        f"/api/v1/patterns/{pid}/colorway", json={"colors": ["#000000", "#ff8800"]}
    )
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["id"]
    assert new_id != pid

    recolored = resp.json()["svg"]
    # New colors present, old accent gone; geometry (rect count) unchanged.
    assert "#ff8800" in recolored and "#00aa33" not in recolored
    assert recolored.count("<rect") == original.count("<rect")
    # Original pattern is untouched.
    assert "#00aa33" in client.get(f"/api/v1/patterns/{pid}").text


def test_recolor_with_named_palette():
    pid = _create_stripe()
    resp = client.post(f"/api/v1/patterns/{pid}/colorway", json={"palette": "navy"})
    assert resp.status_code == 200, resp.text
    fills = _fills(resp.json()["svg"])
    assert set(PALETTES["navy"]) & fills


def test_recolor_requires_exactly_one_source():
    pid = _create_stripe()
    assert client.post(f"/api/v1/patterns/{pid}/colorway", json={}).status_code == 422
    both = {"colors": ["#000000"], "palette": "navy"}
    assert client.post(f"/api/v1/patterns/{pid}/colorway", json=both).status_code == 422


def test_recolor_unknown_palette_rejected():
    pid = _create_stripe()
    resp = client.post(f"/api/v1/patterns/{pid}/colorway", json={"palette": "neon"})
    assert resp.status_code == 422


def test_recolor_unknown_pattern_404():
    resp = client.post("/api/v1/patterns/nope/colorway", json={"palette": "navy"})
    assert resp.status_code == 404


def test_list_palettes():
    resp = client.get("/api/v1/palettes")
    assert resp.status_code == 200
    assert set(resp.json()) == set(PALETTES)


def test_export_width_mm_over_max_rejected():
    pid = _create_stripe()
    resp = client.get(f"/api/v1/patterns/{pid}/export?format=png&width_mm=5000")
    assert resp.status_code == 422
