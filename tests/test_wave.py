"""Curved (wave) lane: torus periodicity, varying tangent, and a standalone wave path
generated into a seamless SVG."""

import math
import xml.etree.ElementTree as ET

import pytest

from app.engine.generate import generate
from app.engine.host import Centerline
from app.validate.intent import IntentInvalid, validate_intent

NS = "{http://www.w3.org/2000/svg}"
TILE = 48.0


def _wave_centerline(wavelength=12.0, amplitude=4.0) -> Centerline:
    return Centerline(
        angle_deg=0.0,
        offset_mm=0.0,
        p=0,
        q=1,
        kind="wave",
        wavelength_mm=wavelength,
        amplitude_mm=amplitude,
    )


def _torus_delta(a: float, b: float, tile: float) -> float:
    d = abs(a - b) % tile
    return min(d, tile - d)


def test_wave_closes_on_torus():
    cl = _wave_centerline()
    length = cl.length_mm(TILE)
    assert length == pytest.approx(TILE)  # horizontal lane closes after one tile
    (x0, y0), t0 = cl.point_at(0.0, TILE)
    (xl, yl), tl = cl.point_at(length, TILE)
    # coincide on the torus (a tiny negative coord wraps to ~tile, not a real seam).
    assert _torus_delta(x0, xl, TILE) < 1e-6
    assert _torus_delta(y0, yl, TILE) < 1e-6
    assert t0 == pytest.approx(tl)  # tangent continuous at closure


def test_wave_tangent_varies_along_curve():
    cl = _wave_centerline(wavelength=12.0, amplitude=4.0)
    _, t_start = cl.point_at(0.0, TILE)  # steepest (sin crest slope)
    _, t_quarter = cl.point_at(3.0, TILE)  # wavelength/4 -> slope 0
    assert abs(t_start - t_quarter) > 1.0


def _wave_intent(path: dict) -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 5,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "ground", "hex": "#10243a"},
                {"id": "accent", "hex": "#ef8a7a"},
            ]
        },
        "colorways": [
            {"id": "default", "mapping": {"ground": "#10243a", "accent": "#ef8a7a"}}
        ],
        "layers": [
            {"id": "ground", "type": "background", "z_order": 0, "params": {"color": "ground"}},
            {
                "id": "vine",
                "type": "motif",
                "z_order": 1,
                "params": {"motif_id": "circle", "size_mm": 1.5, "color": "accent"},
                "placement": {
                    "type": "path_following",
                    "spacing_mm": 6,
                    "rotation": "follow_path",
                    "path": path,
                },
            },
        ],
    }


def test_standalone_wave_generates_pattern_svg():
    cand = generate(
        _wave_intent({"kind": "wave", "angle": 0, "wavelength": 12, "amplitude": 4})
    )
    root = ET.fromstring(cand.svg)
    assert len(root.findall(f".//{NS}pattern")) == 1
    assert len(root.findall(f".//{NS}use")) > 1


def test_standalone_straight_path_generates_pattern_svg():
    cand = generate(_wave_intent({"kind": "straight", "angle": 0}))
    root = ET.fromstring(cand.svg)
    assert len(root.findall(f".//{NS}use")) > 1


def test_wave_wavelength_must_divide_tile():
    with pytest.raises(IntentInvalid):
        validate_intent(
            _wave_intent({"kind": "wave", "angle": 0, "wavelength": 10, "amplitude": 4})
        )


def test_standalone_wave_is_byte_deterministic():
    path = {"kind": "wave", "angle": 0, "wavelength": 12, "amplitude": 4}
    assert generate(_wave_intent(path)).svg == generate(_wave_intent(path)).svg


# --- diagonal waves: closure is L = tile*hypot(p, q), not tile -----------------


def test_diagonal_wave_closes_on_torus():
    # 3-4-5 slope -> L = tile*5 = 240; wavelength 24 divides L, so it closes.
    angle = math.degrees(math.atan2(3, 4))
    cl = Centerline(
        angle_deg=angle, offset_mm=0.0, p=3, q=4, kind="wave",
        wavelength_mm=24.0, amplitude_mm=4.0,
    )
    length = cl.length_mm(TILE)
    assert length == pytest.approx(240.0)
    (x0, y0), t0 = cl.point_at(0.0, TILE)
    (xl, yl), tl = cl.point_at(length, TILE)
    assert _torus_delta(x0, xl, TILE) < 1e-6
    assert _torus_delta(y0, yl, TILE) < 1e-6
    assert t0 == pytest.approx(tl)


def test_pythagorean_diagonal_wave_generates_pattern_svg():
    angle = math.degrees(math.atan2(3, 4))
    cand = generate(
        _wave_intent({"kind": "wave", "angle": angle, "wavelength": 24, "amplitude": 4})
    )
    root = ET.fromstring(cand.svg)
    assert len(root.findall(f".//{NS}use")) > 1


def test_diagonal_wave_rejected_when_wavelength_misses_closure():
    # 45deg -> slope 1/1 -> L = tile*sqrt(2) (irrational); wavelength 12 divides the
    # tile but NOT the closure length, so the lane would not close -> rejected.
    with pytest.raises(IntentInvalid):
        validate_intent(
            _wave_intent({"kind": "wave", "angle": 45, "wavelength": 12, "amplitude": 4})
        )
