import io
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from app.domain.colorway import Colorway
from app.patterns.stripe import StripePattern
from app.render.raster import RasterError, find_renderer, rasterize
from app.render.svg import render_document
from app.texture import KNOWN_TEXTURES, texture_from_name
from app.texture.linen import LinenTexture
from app.texture.noise import NoiseTexture
from app.texture.weave import WeaveTexture
from app.validate.seamless import edge_seam

SVG_NS = "{http://www.w3.org/2000/svg}"
HAS_RENDERER = find_renderer() is not None
RSVG = shutil.which("rsvg-convert")

pytestmark = pytest.mark.filterwarnings("ignore")


def _uniform_stripe(texture: str | None):
    p = StripePattern(tile_mm=10, colorway=Colorway(["#808080"]), widths_mm=[10])
    p.texture = texture_from_name(texture)
    return p


# --- registry -------------------------------------------------------------

def test_registry_maps_known_names():
    assert KNOWN_TEXTURES == {"weave", "linen", "noise"}
    assert isinstance(texture_from_name("weave"), WeaveTexture)
    assert isinstance(texture_from_name("linen"), LinenTexture)
    assert isinstance(texture_from_name("noise"), NoiseTexture)
    assert texture_from_name(None) is None


def test_registry_rejects_unknown():
    with pytest.raises(ValueError):
        texture_from_name("velvet")


# --- filter structure -----------------------------------------------------

def test_weave_filter_has_stitched_turbulence_and_displacement():
    # Standalone fragment has no xmlns, so children are not namespaced.
    f = ET.fromstring(WeaveTexture().to_filter_def("t", 10, 10))
    assert f.tag == "filter"
    assert f.get("filterUnits") == "userSpaceOnUse"
    turb = f.find("feTurbulence")
    assert turb is not None and turb.get("stitchTiles") == "stitch"
    assert f.find("feDisplacementMap") is not None


def test_noise_filter_overlays_grain():
    f = ET.fromstring(NoiseTexture().to_filter_def("t", 10, 10))
    assert f.find("feTurbulence").get("stitchTiles") == "stitch"
    assert f.find("feMerge") is not None


def test_textured_document_embeds_filter_and_reference():
    svg = render_document(_uniform_stripe("weave"))
    root = ET.fromstring(svg)
    assert root.find(f"{SVG_NS}defs/{SVG_NS}filter") is not None
    group = root.find(f"{SVG_NS}defs/{SVG_NS}pattern/{SVG_NS}g")
    assert group is not None and "url(#" in group.get("filter", "")


# --- raster pipeline (needs resvg) ----------------------------------------

@pytest.mark.skipif(not HAS_RENDERER, reason="no SVG renderer (brew install librsvg)")
def test_rasterize_textured_pattern_dimensions():
    svg = render_document(_uniform_stripe("weave"), doc_mm=40)
    data, media = rasterize(svg, "png", dpi=150, width_mm=40)
    assert media == "image/png"
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    assert img.size == (236, 236)  # round(40 / 25.4 * 150)
    assert round(img.info["dpi"][0]) == 150


@pytest.mark.skipif(not HAS_RENDERER, reason="no SVG renderer (brew install librsvg)")
def test_texture_visibly_changes_raster():
    # resvg renders the SVG filter end-to-end: a textured tile differs from the
    # untextured one over the same uniform field.
    from PIL import Image

    def render_arr(texture):
        data, _ = rasterize(
            render_document(_uniform_stripe(texture), doc_mm=10),
            "png",
            dpi=300,
            width_mm=10,
        )
        return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA")).astype(np.int16)

    plain = render_arr(None)
    noisy = render_arr("noise")
    assert float(np.abs(plain - noisy).mean()) > 3.0


@pytest.mark.skipif(RSVG is None, reason="librsvg (rsvg-convert) not installed")
def test_texture_seam_continuous_with_librsvg():
    # librsvg honours feTurbulence stitchTiles, so a textured tile over a
    # uniform field has near-invisible opposite-edge seams.
    from PIL import Image

    svg = render_document(_uniform_stripe("noise"), doc_mm=10)
    data, _ = rasterize(svg, "png", dpi=300, width_mm=10, binary=RSVG)
    arr = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    seam_x, seam_y = edge_seam(arr)
    assert seam_x <= 6 and seam_y <= 6


@pytest.mark.skipif(not HAS_RENDERER, reason="no SVG renderer (brew install librsvg)")
def test_raster_rejects_oversized_request():
    svg = render_document(_uniform_stripe(None), doc_mm=10)
    with pytest.raises(RasterError):
        rasterize(svg, "png", dpi=4000, width_mm=5000)
