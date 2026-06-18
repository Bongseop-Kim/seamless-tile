import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.render.raster import RasterError, find_renderer

client = TestClient(app)

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48">'
    '<rect width="48" height="48" fill="#10243a"/></svg>'
)


def test_export_rejects_bad_dpi():
    resp = client.post(
        "/api/v1/export", json={"svg": SVG, "dpi": 0, "width_mm": 48}
    )
    assert resp.status_code == 400


def test_export_rejects_oversize_width():
    resp = client.post(
        "/api/v1/export", json={"svg": SVG, "dpi": 300, "width_mm": 999999}
    )
    assert resp.status_code == 400


@pytest.mark.skipif(find_renderer() is None, reason="no SVG renderer installed")
def test_export_png_success():
    resp = client.post(
        "/api/v1/export",
        json={"svg": SVG, "format": "png", "dpi": 300, "width_mm": 48},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_export_renderer_failure_returns_5xx(monkeypatch):
    def boom(*args, **kwargs):
        raise RasterError("renderer exploded")

    monkeypatch.setattr("app.api.routes.export.rasterize", boom)
    resp = client.post(
        "/api/v1/export",
        json={"svg": SVG, "format": "png", "dpi": 300, "width_mm": 48},
    )
    assert resp.status_code == 502
    assert "renderer exploded" in str(resp.json()["detail"])
