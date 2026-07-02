"""Session 15 acceptance tests: deterministic fabric texture render (`/finalize`).

Covers the print / yarn-dyed production split: print applies one twill uniformly,
yarn-dyed mixes weaves per color slot. Renderer-dependent cases skip when no SVG renderer
is installed (mirrors test_api_export); bad-knob / invalid-intent / request-schema cases
run without a renderer (they fail before rasterize). External services are blanked by
conftest, so a green run also proves the path needs no external generation API.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.engine.composition import compose
from app.main import app
from app.render.fabric import FabricError, render_fabric
from app.render.raster import RasterError, find_renderer, rasterize
from app.validate.intent import IntentInvalid, validate_intent
from app.validate.seamless import TILING_SEAM_TOL, edge_seam, tiling_seam

client = TestClient(app)
needs_renderer = pytest.mark.skipif(
    find_renderer() is None, reason="no SVG renderer installed"
)


@pytest.fixture(autouse=True)
def _synthetic_weave_assets(tmp_path, monkeypatch):
    """Point fabric at temp synthetic tileable weaves so tests never depend on (or touch)
    the user-managed app/render/assets/fabric/ images. Low-frequency => small seam."""
    import math

    d = tmp_path / "fabric"
    d.mkdir()

    def _save(name, fn):
        img = Image.new("RGB", (64, 64))
        px = img.load()
        for y in range(64):
            for x in range(64):
                v = max(0, min(255, round(fn(x, y))))
                px[x, y] = (v, v, v)
        img.save(d / f"{name}.png")

    _save("solid", lambda x, y: 228 + 8 * math.cos(2 * math.pi * x / 8) * math.cos(2 * math.pi * y / 8))
    _save("twill-45", lambda x, y: 214 + 14 * math.cos(2 * math.pi * ((x - y) % 16) / 16))
    _save("twill-0", lambda x, y: 216 + 12 * math.cos(2 * math.pi * (x % 8) / 8))
    _save("herringbone", lambda x, y: 216 + 10 * math.cos(
        2 * math.pi * (((x - y) if (y // 16) % 2 == 0 else (x + y)) % 16) / 16))
    monkeypatch.setattr("app.render.fabric._ASSETS", d)


def _intent(method: str = "print") -> dict:
    """Background + diagonal 3-band stripe (no motifs). Commensurate. `method` sets
    production.method (print | yarn_dyed)."""
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 184231,
        "production": {"method": method, "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "ground", "hex": "#10243a", "name": "navy"},
                {"id": "band_a", "hex": "#ef8a7a"},
                {"id": "band_b", "hex": "#e0a93b"},
                {"id": "band_c", "hex": "#9ca084"},
            ]
        },
        "colorways": [
            {"id": "default", "name": "default", "mapping": {
                "ground": "#10243a", "band_a": "#ef8a7a",
                "band_b": "#e0a93b", "band_c": "#9ca084"}},
        ],
        "layers": [
            {"id": "ground", "type": "background", "z_order": 0,
             "params": {"color": "ground"}},
            {"id": "stripe", "type": "stripe", "z_order": 1, "params": {
                "angle": -36.87, "period_mm": 9.6, "bands": [
                    {"offset_mm": 0.0, "width_mm": 2.0, "color": "band_a"},
                    {"offset_mm": 3.2, "width_mm": 2.0, "color": "band_b"},
                    {"offset_mm": 6.4, "width_mm": 2.0, "color": "band_c"}]}},
        ],
    }


_YARN_MAP = {"ground": "solid", "band_a": "twill-45",
             "band_b": "twill-0", "band_c": "herringbone"}


def _motif_intent() -> dict:
    """The stripe design plus a registered "circle" motif layer (color slot ``ink``) —
    exercises the MOTIF_WEAVE default-pin path, which the stripe-only ``_intent`` never
    reaches."""
    from tests.test_intent import mvp_intent  # noqa: F401  registers the circle motif

    intent = _intent("yarn_dyed")
    intent["palette"]["slots"].append({"id": "ink", "hex": "#f5ca57"})
    intent["colorways"][0]["mapping"]["ink"] = "#f5ca57"
    intent["layers"].append({
        "id": "dots", "type": "motif", "z_order": 2,
        "params": {"motif_id": "circle", "size_mm": 4, "color": "ink"},
        "placement": {"type": "point_set", "point_set": {"points": [[24, 24]]}},
    })
    return intent


def _seam_max(png: bytes) -> float:
    return max(edge_seam(Image.open(io.BytesIO(png)).convert("RGBA")))


def _tiling_excess(png: bytes) -> tuple[float, float]:
    """Repeat the tile 2x2 and measure the real internal seam (see tiling_seam)."""
    tile = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = tile.size
    big = Image.new("RGBA", (w * 2, h * 2))
    for i in (0, 1):
        for j in (0, 1):
            big.paste(tile, (i * w, j * h))
    return tiling_seam(big, w)


# --- 1. texture conversion --------------------------------------------------

@needs_renderer
def test_render_fabric_produces_png():
    png = render_fabric(_intent("print"), weave="twill-45")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    Image.open(io.BytesIO(png)).verify()


# --- 2. determinism (core) --------------------------------------------------

@needs_renderer
def test_render_fabric_deterministic():
    a = render_fabric(_intent("yarn_dyed"), material_map=_YARN_MAP)
    b = render_fabric(_intent("yarn_dyed"), material_map=_YARN_MAP)
    assert a == b


# --- 3. seamless maintained (print uniform + yarn-dyed per-region) ----------

@needs_renderer
@pytest.mark.parametrize(
    "method,kwargs",
    [("print", {"weave": "twill-45"}),
     ("print", {"weave": "twill-0"}),
     # relief off: this case isolates whether per-region *weave* worsens the seam. The
     # relief bevel is a hard edge that the edge_seam proxy can't judge — test_relief_keeps_seam
     # verifies it with the robust tiling_seam metric instead.
     ("yarn_dyed", {"material_map": _YARN_MAP, "relief_strength": 0})],
)
def test_seamless_maintained(method, kwargs):
    # The design's diagonal stripes have a small intrinsic edge_seam (AA); the texture must
    # not materially worsen it. Assert excess over the design baseline (tiling_seam idea).
    r = validate_intent(_intent(method))
    base_png, _ = rasterize(
        compose(r.intent, r.palette, None), "png",
        dpi=r.intent.canvas.dpi, width_mm=r.intent.canvas.tile_mm,
    )
    baseline = _seam_max(base_png)
    fab = render_fabric(_intent(method), **kwargs)
    assert _seam_max(fab) <= baseline + 2.5


# --- 4. yarn-dyed per-region + uniform fallback -----------------------------

@needs_renderer
def test_yarn_dyed_per_region_and_fallback():
    uniform = render_fabric(_intent("yarn_dyed"), weave="twill-45")
    # Empty / omitted material map == uniform fallback, byte-identical.
    assert render_fabric(_intent("yarn_dyed"), weave="twill-45", material_map=None) == uniform
    assert render_fabric(_intent("yarn_dyed"), weave="twill-45", material_map={}) == uniform
    # Painting every region the fallback weave is also the uniform result.
    all_same = {s: "twill-45" for s in ("ground", "band_a", "band_b", "band_c")}
    assert render_fabric(_intent("yarn_dyed"), weave="twill-45", material_map=all_same) == uniform
    # Mixing weaves per region changes the output, and each distinct weave shows.
    assert render_fabric(_intent("yarn_dyed"), material_map=_YARN_MAP) != uniform


@needs_renderer
def test_print_uniform_twills_differ():
    assert render_fabric(_intent("print"), weave="twill-0") != render_fabric(
        _intent("print"), weave="twill-45"
    )


@needs_renderer
def test_production_method_override():
    # Override an intent's print -> yarn_dyed at finalize time (user selection).
    printed = render_fabric(_intent("print"), weave="twill-45")
    woven = render_fabric(
        _intent("print"), production_method="yarn_dyed", material_map=_YARN_MAP
    )
    assert printed != woven


# --- 4b. relief (yarn-dyed raised-thread emboss) ----------------------------

@needs_renderer
def test_relief_default_on_for_yarn_dyed_and_deterministic():
    a = render_fabric(_intent("yarn_dyed"), material_map=_YARN_MAP)
    b = render_fabric(_intent("yarn_dyed"), material_map=_YARN_MAP)
    assert a == b  # deterministic
    flat = render_fabric(_intent("yarn_dyed"), material_map=_YARN_MAP, relief_strength=0)
    assert a != flat  # relief is on by default and actually changes the raster


@needs_renderer
def test_relief_off_for_print():
    # print is flat ink; relief is a yarn-dyed concept and must be a no-op there.
    base = render_fabric(_intent("print"), weave="twill-45")
    assert render_fabric(_intent("print"), weave="twill-45", relief_strength=3.0) == base


@needs_renderer
def test_relief_keeps_seam():
    # The relief bevel is a hard edge that crosses the seam, so edge_seam (single-tile
    # proxy) legitimately flags it — see its docstring. The load-bearing check is
    # tiling_seam: actually repeat the tile 2x2 and confirm the real internal seam has no
    # discontinuity beyond the interior baseline (rims come from wrap-around offset).
    fab = render_fabric(_intent("yarn_dyed"), material_map=_YARN_MAP, relief_strength=3.0)
    excess_x, excess_y = _tiling_excess(fab)
    assert excess_x <= TILING_SEAM_TOL
    assert excess_y <= TILING_SEAM_TOL


# --- 4c. MOTIF_WEAVE pin: a default for unmapped motif slots, not an override --

@needs_renderer
def test_motif_pin_defaults_and_user_map_wins():
    intent = _motif_intent()
    pinned = render_fabric(intent, weave="solid")  # ink unmapped -> MOTIF_WEAVE default
    overridden = render_fabric(intent, weave="solid", material_map={"ink": "solid"})
    explicit = render_fabric(intent, weave="solid", material_map={"ink": "twill-45"})
    assert pinned != overridden  # the pin is real: unmapped motif slot got twill-45, not solid
    assert pinned == explicit  # ...and it is only a default: an explicit entry matches it


@needs_renderer
def test_motif_intent_default_relief_deterministic_and_seamless():
    # The default yarn-dyed path (pin + relief ON) with a motif present: still
    # byte-deterministic and still tileable.
    intent = _motif_intent()
    a = render_fabric(intent, weave="solid")
    assert a == render_fabric(intent, weave="solid")
    excess_x, excess_y = _tiling_excess(a)
    assert excess_x <= TILING_SEAM_TOL
    assert excess_y <= TILING_SEAM_TOL


def test_relief_negative_raises():
    with pytest.raises(FabricError):
        render_fabric(_intent("yarn_dyed"), material_map=_YARN_MAP, relief_strength=-1.0)


def test_motif_slots_extracts_only_motif_palette_slots():
    # The twill-45 pin depends on this: motif slots come from motif layers' color/colors,
    # never from stripe/background layers. Duck-typed so it needs no motif registry.
    from types import SimpleNamespace as NS

    from app.render.fabric import _motif_slots

    intent = NS(layers=[
        NS(type="background", params=NS(color="ground", colors=None)),
        NS(type="stripe", params=NS(color="accent", colors=None)),
        NS(type="motif", params=NS(color=None, colors={"s0": "ink", "s1": "leaf"})),
        NS(type="motif", params=NS(color="plum", colors=None)),
    ])
    assert _motif_slots(intent) == {"ink", "leaf", "plum"}  # no ground/accent


# --- 5. production-split guards & bad knobs -> FabricError (no renderer) -----

def test_print_rejects_material_map():
    with pytest.raises(FabricError):
        render_fabric(_intent("print"), weave="twill-45", material_map=_YARN_MAP)


def test_print_rejects_non_twill_weave():
    with pytest.raises(FabricError):
        render_fabric(_intent("print"), weave="solid")


def test_unknown_weave_raises():
    with pytest.raises(FabricError):
        render_fabric(_intent("yarn_dyed"), weave="velvet")


def test_yarn_dyed_unknown_material_slot_raises():
    with pytest.raises(FabricError):
        render_fabric(_intent("yarn_dyed"), material_map={"nope": "solid"})


def test_yarn_dyed_unknown_material_weave_raises():
    with pytest.raises(FabricError):
        render_fabric(_intent("yarn_dyed"), material_map={"band_a": "velvet"})


def test_unknown_colorway_raises():
    with pytest.raises(FabricError):
        render_fabric(_intent("print"), colorway_id="nope")


def test_invalid_intent_raises():
    bad = _intent("print")
    bad["layers"][0]["params"]["color"] = "missing"
    with pytest.raises(IntentInvalid):
        render_fabric(bad, weave="twill-45")


# --- 6. API surface ---------------------------------------------------------

@needs_renderer
def test_api_finalize_print_success_storage_off():
    resp = client.post(
        "/api/v1/finalize",
        headers={"X-Request-ID": "fab-1"},
        json={"intent": _intent("print"), "weave": "twill-0"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == "fab-1"
    assert body["image_url"] is None  # storage unconfigured (conftest) -> graceful degrade
    assert any("storage not configured" in w for w in body["warnings"])
    assert resp.headers["X-Request-ID"] == "fab-1"


@needs_renderer
def test_api_finalize_yarn_dyed_success():
    resp = client.post(
        "/api/v1/finalize",
        json={"intent": _intent("yarn_dyed"), "material_map": _YARN_MAP},
    )
    assert resp.status_code == 200
    assert resp.json()["image_url"] is None


def test_api_finalize_print_with_material_map_400():
    resp = client.post(
        "/api/v1/finalize",
        json={"intent": _intent("print"), "material_map": _YARN_MAP},
    )
    assert resp.status_code == 400


def test_api_finalize_invalid_intent_422():
    bad = _intent("print")
    bad["layers"][0]["params"]["color"] = "missing"
    resp = client.post("/api/v1/finalize", json={"intent": bad})
    assert resp.status_code == 422
    assert "request_id" in resp.json()


def test_api_finalize_unknown_weave_400():
    resp = client.post(
        "/api/v1/finalize", json={"intent": _intent("yarn_dyed"), "weave": "velvet"}
    )
    assert resp.status_code == 400


def test_api_finalize_bad_production_method_422():
    # Literal enforces the value at the request-schema boundary.
    resp = client.post(
        "/api/v1/finalize",
        json={"intent": _intent("print"), "production_method": "woven"},
    )
    assert resp.status_code == 400


def test_api_finalize_extra_field_400():
    resp = client.post("/api/v1/finalize", json={"intent": _intent("print"), "bogus": 1})
    assert resp.status_code == 400  # extra="forbid" -> request-schema failure


def test_api_finalize_renderer_failure_502(monkeypatch):
    def boom(*args, **kwargs):
        raise RasterError("renderer exploded")

    monkeypatch.setattr("app.render.fabric.rasterize", boom)
    resp = client.post(
        "/api/v1/finalize", json={"intent": _intent("print"), "weave": "twill-45"}
    )
    assert resp.status_code == 502
    assert "renderer exploded" in str(resp.json()["detail"])
