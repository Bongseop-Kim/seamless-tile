"""Tier1 structural-heuristic gate (spec §8) inside ``normalize_motif_svg``.

Covers the three checks added beyond drawable/degenerate: bbox aspect ratio (#3, pure
geometry), render error (#4), and the bbox-overflow ``edge_seam`` guard (#5). The
render-dependent checks (#4/#5) are exercised with monkeypatched renderer/metric so the
suite is deterministic and does not require librsvg to be installed.
"""

import io

import pytest
from PIL import Image

from app.motifs.registry import normalize_motif_svg


def _svg(inner: str, viewbox: str = "0 0 100 100") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{inner}</svg>'


_CIRCLE = _svg('<circle cx="50" cy="50" r="40" fill="#000"/>')


# --- #3 bbox aspect ratio (pure geometry, no renderer) ------------------------


def test_aspect_ratio_rejects_thin_motif():
    thin = _svg('<rect x="0" y="49" width="100" height="1" fill="#000"/>')
    with pytest.raises(ValueError, match="aspect ratio"):
        normalize_motif_svg(thin)


def test_aspect_ratio_independent_of_render_check():
    # #3 is geometry-only: it still fires with the render-based checks disabled.
    thin = _svg('<rect x="0" y="40" width="100" height="2" fill="#000"/>')
    with pytest.raises(ValueError, match="aspect ratio"):
        normalize_motif_svg(thin, render_check=False)


def test_aspect_ratio_custom_threshold():
    rect = _svg('<rect x="0" y="40" width="100" height="20" fill="#000"/>')  # 5:1
    assert normalize_motif_svg(rect, render_check=False).id  # default cap 20 -> ok
    with pytest.raises(ValueError, match="aspect ratio"):
        normalize_motif_svg(rect, max_aspect_ratio=4.0, render_check=False)


# --- contained / box-filling motifs are not false-rejected --------------------


def test_contained_motif_passes():
    assert normalize_motif_svg(_CIRCLE).id


def test_box_filling_motif_not_false_rejected():
    # A shape filling its own bbox must still pass: the render gate renders into a
    # margined tile so a contained motif leaves the border transparent (edge_seam ~ 0).
    full = _svg('<rect x="0" y="0" width="100" height="100" fill="#000"/>')
    assert normalize_motif_svg(full).id


# --- #4 render error -> ValueError --------------------------------------------


def test_render_error_maps_to_valueerror(monkeypatch):
    from app.render.raster import RasterError

    monkeypatch.setattr("app.render.raster.find_renderer", lambda *a, **k: "/fake/rsvg")

    def boom(*a, **k):
        raise RasterError("boom")

    monkeypatch.setattr("app.render.raster.rasterize", boom)
    with pytest.raises(ValueError, match="failed to render"):
        normalize_motif_svg(_CIRCLE)


# --- #5 bbox-overflow edge_seam guard -----------------------------------------


def _stub_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (8, 8)).save(buf, format="PNG")
    return buf.getvalue()


def test_overflow_seam_rejects(monkeypatch):
    png = _stub_png()
    monkeypatch.setattr("app.render.raster.find_renderer", lambda *a, **k: "/fake/rsvg")
    monkeypatch.setattr("app.render.raster.rasterize", lambda *a, **k: (png, "image/png"))
    monkeypatch.setattr("app.validate.seamless.edge_seam", lambda arr: (99.0, 0.0))
    with pytest.raises(ValueError, match="overflows"):
        normalize_motif_svg(_CIRCLE)


def test_seam_within_tolerance_passes(monkeypatch):
    png = _stub_png()
    monkeypatch.setattr("app.render.raster.find_renderer", lambda *a, **k: "/fake/rsvg")
    monkeypatch.setattr("app.render.raster.rasterize", lambda *a, **k: (png, "image/png"))
    monkeypatch.setattr("app.validate.seamless.edge_seam", lambda arr: (1.0, 0.5))
    assert normalize_motif_svg(_CIRCLE, edge_seam_tol=2.0).id


# --- graceful degradation + determinism ---------------------------------------


def test_render_checks_skipped_without_renderer(monkeypatch):
    monkeypatch.setattr("app.render.raster.find_renderer", lambda *a, **k: None)
    # Even a metric that would reject is never consulted when no renderer exists.
    monkeypatch.setattr("app.validate.seamless.edge_seam", lambda arr: (99.0, 99.0))
    assert normalize_motif_svg(_CIRCLE).id


def test_render_check_off_bypasses_render(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("rasterize must not be called when render_check is off")

    monkeypatch.setattr("app.render.raster.rasterize", boom)
    assert normalize_motif_svg(_CIRCLE, render_check=False).id


def test_gate_is_deterministic():
    a = normalize_motif_svg(_CIRCLE)
    b = normalize_motif_svg(_CIRCLE)
    assert a.id == b.id
    assert a.symbol == b.symbol
