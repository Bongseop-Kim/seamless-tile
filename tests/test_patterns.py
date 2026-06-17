import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.stripe import StripeRequest
from app.main import app
from app.patterns.composed_stripe import ComposedStripePattern
from app.render.raster import find_renderer, rasterize
from app.render.svg import render_document
from app.validate.seamless import edge_seam

client = TestClient(app)

HAS_RENDERER = find_renderer() is not None

SVG_NS = "{http://www.w3.org/2000/svg}"

STRIPE = {
    "widths_mm": [10, 10],
    "colors": ["#ffffff", "#00aa33"],
    "angle": -45,
    "tile_mm": 20,
}
CHECK = {"widths_mm": [5, 5], "colors": ["#cc2222"], "tile_mm": 20}
DOT = {"radius_mm": 3, "spacing_mm": 10, "colors": ["#102030", "#ffffff"]}
HERRINGBONE = {"stroke_mm": 2, "pitch_mm": 10, "colors": ["#222222"], "tile_mm": 40}
COMPOSED_STRIPE = {
    "tile_mm": 48,
    "angle": -32,
    "background_color": "#10243a",
    "stripes": [
        {
            "offset_mm": 6,
            "width_mm": 18,
            "color": "#0a1a2b",
            "edge_lines": [
                {
                    "position": "start",
                    "width_mm": 0.8,
                    "color": "#e02b22",
                    "style": "dotted",
                    "dot_length_mm": 1.2,
                    "gap_mm": 1.2,
                    "dot_shape": "circle",
                },
                {
                    "position": "center",
                    "width_mm": 0.4,
                    "color": "#f0f2ee",
                    "style": "solid",
                },
            ],
        },
        {"offset_mm": 30, "width_mm": 6, "color": "#526a89", "opacity": 0.65},
    ],
}
LAYERED_DOT = {
    "tile_mm": 48,
    "background_color": "#f7f3eb",
    "layers": [
        {
            "shape": "circle",
            "size_mm": 4,
            "color": "#16233f",
            "spacing_x_mm": 12,
            "spacing_y_mm": 12,
            "repeat": "half_drop",
        },
        {
            "shape": "diamond",
            "size_mm": 2,
            "color": "#b23a48",
            "spacing_x_mm": 24,
            "spacing_y_mm": 24,
            "offset_x_mm": 6,
            "offset_y_mm": 6,
        },
        {
            "shape": "teardrop",
            "size_mm": 3,
            "color": "#277a6f",
            "spacing_x_mm": 16,
            "spacing_y_mm": 16,
            "offset_x_mm": 8,
            "offset_y_mm": 8,
        },
    ],
}

CASES = [
    ("stripe", STRIPE),
    ("check", CHECK),
    ("dot", DOT),
    ("herringbone", HERRINGBONE),
]


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


def test_removed_texture_option_rejected():
    for kind, body in CASES:
        resp = client.post(f"/api/v1/patterns/{kind}", json={**body, "texture": "noise"})
        assert resp.status_code == 422


def test_stripe_tile_must_be_multiple_of_period():
    body = dict(STRIPE, tile_mm=25)  # period 20, 25 not a multiple
    assert client.post("/api/v1/patterns/stripe", json=body).status_code == 422


def test_stripe_angle_must_be_diagonal():
    body = dict(STRIPE, angle=0)
    assert client.post("/api/v1/patterns/stripe", json=body).status_code == 422


def test_dot_radius_must_fit_spacing():
    body = dict(DOT, radius_mm=8)  # spacing 10 -> max 5
    assert client.post("/api/v1/patterns/dot", json=body).status_code == 422


def test_composed_stripe_supports_mixed_width_and_dotted_lines():
    data = _create("stripe", COMPOSED_STRIPE)
    pattern = ET.fromstring(data["svg"]).find(f"{SVG_NS}defs/{SVG_NS}pattern")
    assert pattern is not None
    fills = {el.get("fill") for el in pattern.iter() if el.get("fill")}
    strokes = {el.get("stroke") for el in pattern.iter() if el.get("stroke")}
    assert {"#10243a", "#e02b22"} <= fills
    assert {"#0a1a2b", "#526a89", "#f0f2ee"} <= strokes
    assert len(pattern.findall(f".//{SVG_NS}circle")) >= 1
    assert len(pattern.findall(f".//{SVG_NS}line")) >= 2


def test_dot_layers_support_spacing_size_and_shapes():
    data = _create("dot", LAYERED_DOT)
    pattern = ET.fromstring(data["svg"]).find(f"{SVG_NS}defs/{SVG_NS}pattern")
    assert pattern is not None
    fills = {el.get("fill") for el in pattern.iter() if el.get("fill")}
    assert {"#f7f3eb", "#16233f", "#b23a48", "#277a6f"} <= fills
    assert len(pattern.findall(f".//{SVG_NS}circle")) >= 1
    assert len(pattern.findall(f".//{SVG_NS}polygon")) >= 1
    assert len(pattern.findall(f".//{SVG_NS}path")) >= 1


def test_dot_layers_reject_size_larger_than_spacing():
    body = {
        "tile_mm": 20,
        "layers": [
            {
                "shape": "diamond",
                "size_mm": 12,
                "color": "#111111",
                "spacing_x_mm": 10,
                "spacing_y_mm": 10,
            }
        ],
    }
    assert client.post("/api/v1/patterns/dot", json=body).status_code == 422


def test_composed_stripe_dotted_line_requires_pitch():
    body = dict(COMPOSED_STRIPE)
    body["stripes"] = [
        {
            "offset_mm": 8,
            "width_mm": 14,
            "color": "#0a1a2b",
            "edge_lines": [
                {
                    "position": "start",
                    "width_mm": 0.7,
                    "color": "#e02b22",
                    "style": "dotted",
                }
            ],
        }
    ]
    assert client.post("/api/v1/patterns/stripe", json=body).status_code == 422


def test_composed_stripe_recolor_changes_colors_keeps_geometry():
    pid = _create("stripe", COMPOSED_STRIPE)["id"]
    original = client.get(f"/api/v1/patterns/{pid}").text
    resp = client.post(
        f"/api/v1/patterns/{pid}/colorway",
        json={"colors": ["#000000", "#111111", "#222222", "#333333", "#444444"]},
    )
    assert resp.status_code == 200, resp.text
    recolored = resp.json()["svg"]
    assert "#444444" in recolored and "#e02b22" not in recolored
    assert recolored.count("<rect") == original.count("<rect")
    assert recolored.count("<circle") == original.count("<circle")


@pytest.mark.skipif(not HAS_RENDERER, reason="no SVG renderer (brew install librsvg)")
def test_composed_stripe_raster_edges_are_continuous():
    import io

    import numpy as np
    from PIL import Image

    req = StripeRequest.model_validate(COMPOSED_STRIPE)
    pattern = ComposedStripePattern(
        tile_mm=req.tile_mm,
        background_color=req.background_color,
        stripes=req.stripes,
        angle=req.angle,
    )
    width, height = pattern.tile_size()
    svg = render_document(pattern, doc_mm=width)
    data, _ = rasterize(svg, "png", dpi=180, width_mm=width, height_mm=height)
    arr = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    seam_x, seam_y = edge_seam(arr)
    assert seam_x <= 6 and seam_y <= 6
