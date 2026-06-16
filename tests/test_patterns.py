import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.render.raster import find_renderer

client = TestClient(app)

HAS_RENDERER = find_renderer() is not None

SVG_NS = "{http://www.w3.org/2000/svg}"

STRIPE = {"widths_mm": [10, 10], "colors": ["#ffffff", "#00aa33"], "angle": 0, "tile_mm": 20}
CHECK = {"widths_mm": [5, 5], "colors": ["#cc2222"], "tile_mm": 20}
DOT = {"radius_mm": 3, "spacing_mm": 10, "colors": ["#102030", "#ffffff"]}
HERRINGBONE = {"stroke_mm": 2, "pitch_mm": 10, "colors": ["#222222"], "tile_mm": 40}

CASES = [("stripe", STRIPE), ("check", CHECK), ("dot", DOT), ("herringbone", HERRINGBONE)]


def _create(kind: str, body: dict) -> dict:
    resp = client.post(f"/api/v1/patterns/{kind}", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_returns_id_and_valid_svg():
    for kind, body in CASES:
        data = _create(kind, body)
        assert "id" in data and data["id"]
        root = ET.fromstring(data["svg"])  # raises on malformed XML
        assert root.tag == f"{SVG_NS}svg"
        assert root.get("width", "").endswith("mm")


def test_get_pattern_returns_svg():
    data = _create("stripe", STRIPE)
    resp = client.get(f"/api/v1/patterns/{data['id']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    ET.fromstring(resp.text)


def test_get_unknown_pattern_404():
    assert client.get("/api/v1/patterns/does-not-exist").status_code == 404


def test_export_svg_ok():
    data = _create("dot", DOT)
    resp = client.get(f"/api/v1/patterns/{data['id']}/export?format=svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")


@pytest.mark.skipif(not HAS_RENDERER, reason="no SVG renderer (brew install librsvg)")
def test_export_png_and_tiff():
    pid = _create("dot", DOT)["id"]
    png = client.get(f"/api/v1/patterns/{pid}/export?format=png&dpi=150&width_mm=40")
    assert png.status_code == 200, png.text
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"

    tiff = client.get(f"/api/v1/patterns/{pid}/export?format=tiff&dpi=150&width_mm=40")
    assert tiff.status_code == 200, tiff.text
    assert tiff.headers["content-type"] == "image/tiff"


def test_export_dpi_over_max_rejected():
    pid = _create("dot", DOT)["id"]
    resp = client.get(f"/api/v1/patterns/{pid}/export?format=png&dpi=5000")
    assert resp.status_code == 422


def test_invalid_color_rejected():
    body = dict(STRIPE, colors=["not-a-color"])
    assert client.post("/api/v1/patterns/stripe", json=body).status_code == 422


def test_stripe_tile_must_be_multiple_of_period():
    body = dict(STRIPE, tile_mm=25)  # period 20, 25 not a multiple
    assert client.post("/api/v1/patterns/stripe", json=body).status_code == 422


def test_dot_radius_must_fit_spacing():
    body = dict(DOT, radius_mm=8)  # spacing 10 -> max 5
    assert client.post("/api/v1/patterns/dot", json=body).status_code == 422
