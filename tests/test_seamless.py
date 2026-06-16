import xml.etree.ElementTree as ET

import numpy as np

from app.domain.colorway import Colorway
from app.domain.repeat import RepeatMode, placements
from app.patterns.dot import DotPattern
from app.patterns.herringbone import HerringbonePattern
from app.patterns.stripe import StripePattern
from app.render.svg import render_document
from app.validate.seamless import seamless_diff

SVG_NS = "{http://www.w3.org/2000/svg}"


def _parse(svg: str) -> ET.Element:
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG_NS}svg"
    return root


def _find_pattern(svg: str) -> ET.Element:
    root = _parse(svg)
    pattern = root.find(f"{SVG_NS}defs/{SVG_NS}pattern")
    assert pattern is not None
    assert pattern.get("patternUnits") == "userSpaceOnUse"
    return pattern


# --- seamless_diff utility ------------------------------------------------

def test_seamless_diff_zero_on_uniform():
    tile = np.zeros((16, 16, 4), dtype=np.uint8)
    assert seamless_diff(tile) == (0.0, 0.0)


def test_seamless_diff_detects_horizontal_discontinuity():
    tile = np.zeros((16, 16, 4), dtype=np.uint8)
    tile[:, 8:] = 255  # left half black, right half white
    seam_x, _ = seamless_diff(tile)
    assert seam_x > 100


def test_seamless_diff_detects_vertical_discontinuity():
    tile = np.zeros((16, 16, 4), dtype=np.uint8)
    tile[8:, :] = 255
    _, seam_y = seamless_diff(tile)
    assert seam_y > 100


# --- repeat lattice -------------------------------------------------------

def test_block_is_single_stamp():
    tw, th, places = placements(10, 10, RepeatMode.block)
    assert (tw, th) == (10, 10)
    assert places == [(0.0, 0.0)]


def test_half_drop_doubles_width_with_wrap_copy():
    tw, th, places = placements(10, 10, RepeatMode.half_drop)
    assert (tw, th) == (20, 10)
    assert (10, 5.0) in places and (10, -5.0) in places


def test_brick_doubles_height_with_wrap_copy():
    tw, th, places = placements(10, 10, RepeatMode.brick)
    assert (tw, th) == (10, 20)
    assert (5.0, 10) in places and (-5.0, 10) in places


# --- structural seamlessness of patterns ----------------------------------

def test_dot_half_drop_compound_tile_and_stamps():
    p = DotPattern(spacing_mm=10, radius_mm=3, colorway=Colorway(["#102030", "#ffffff"]))
    pattern = _find_pattern(render_document(p))
    assert pattern.get("width") == "20"  # 2 * spacing
    assert pattern.get("height") == "10"
    assert len(pattern.findall(f"{SVG_NS}g")) == 3  # base + two wrap stamps


def test_stripe_bands_fill_full_height():
    p = StripePattern(tile_mm=40, colorway=Colorway(["#ffffff", "#00aa33"]), widths_mm=[10, 10])
    pattern = _find_pattern(render_document(p))
    rects = pattern.findall(f".//{SVG_NS}rect")
    assert rects, "stripe should emit band rects"
    for rect in rects:
        assert rect.get("height") == "40"  # full-bleed -> vertically seamless


def test_herringbone_strokes_present_and_commensurate():
    pitch = 10
    p = HerringbonePattern(tile_mm=40, colorway=Colorway(["#222222"]), stroke_mm=2, pitch_mm=pitch)
    assert p.tile_mm % pitch == 0
    pattern = _find_pattern(render_document(p))
    paths = pattern.findall(f".//{SVG_NS}path")
    assert len(paths) == p.tile_mm / pitch
