import pytest
from fastapi.testclient import TestClient

from app.engine.palette import Colorway, PALETTES, is_hex_color, resolve_palette
from app.main import app

client = TestClient(app)


def test_colorway_validates_colors():
    assert Colorway(["#ffffff", "#00aa33"]).colors == ("#ffffff", "#00aa33")
    with pytest.raises(ValueError):
        Colorway(["not-a-color"])


def test_colorway_indexes_cyclically():
    colorway = Colorway(["#111111", "#222222"])
    assert colorway[0] == "#111111"
    assert colorway[2] == "#111111"


def test_hex_color_validation():
    assert is_hex_color("#fff")
    assert is_hex_color("#ffffff")
    assert not is_hex_color("ffffff")


def test_resolve_palette():
    assert resolve_palette("navy") == PALETTES["navy"]
    with pytest.raises(ValueError):
        resolve_palette("neon")


def test_list_palettes():
    resp = client.get("/api/v1/palettes")
    assert resp.status_code == 200
    assert set(resp.json()) == set(PALETTES)
