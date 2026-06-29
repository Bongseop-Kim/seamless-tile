"""Offline end-to-end: a Recraft-style motif -> seamless tile.

Registers the motif from a fixed multicolor SVG instead of calling the network,
so it stays in the deterministic test suite.
"""

import xml.etree.ElementTree as ET

import pytest

from app.adapters.recraft import _flatten_unsuitable
from app.engine.generate import generate
from app.motifs.registry import MOTIFS, normalize_motif_svg, register_motif

NS = "{http://www.w3.org/2000/svg}"

# A "pig riding a bicycle" stand-in: a Recraft-shaped SVG (rgb() fills + full-canvas
# background) so the gate (flatten + background strip) and tight-bbox framing both run.
_RECRAFT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" preserveAspectRatio="none">'
    '<path d="M0 0 L100 0 L100 100 L0 100 Z" fill="rgb(245,239,227)"/>'  # full-canvas background
    '<rect x="30" y="30" width="40" height="24" fill="rgb(239,154,166)"/>'  # body
    '<circle cx="36" cy="68" r="12" fill="rgb(46,42,42)"/>'  # wheel
    '<circle cx="64" cy="68" r="12" fill="rgb(46,42,42)"/>'  # wheel
    "</svg>"
)


@pytest.fixture(autouse=True)
def _clean():
    def _purge():
        for key in [k for k in MOTIFS if k.startswith("recraft-")]:
            del MOTIFS[key]

    _purge()
    yield
    _purge()


def _register_motif() -> str:
    motif = normalize_motif_svg(_flatten_unsuitable(_RECRAFT_SVG), max_color_slots=6)
    return register_motif(motif, source="recraft")


def _build_tile_intent(
    motif_id: str,
    *,
    tile_mm: float = 48.0,
    cell_mm: float = 24.0,
    size_mm: float = 18.0,
    bg_hex: str = "#f5efe3",
) -> dict:
    return {
        "intent_version": 1,
        "canvas": {"tile_mm": tile_mm, "dpi": 300},
        "seed": 7,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "bg", "hex": bg_hex},
                {"id": "p0", "hex": "#ef9aa6"},
                {"id": "p1", "hex": "#2e2a2a"},
            ],
        },
        "colorways": [{
            "id": "default",
            "name": "default",
            "mapping": {"bg": bg_hex, "p0": "#ef9aa6", "p1": "#2e2a2a"},
        }],
        "layers": [
            {"id": "bg", "type": "background", "z_order": 0, "params": {"color": "bg"}},
            {
                "id": "motif",
                "type": "motif",
                "z_order": 1,
                "params": {
                    "motif_id": motif_id,
                    "size_mm": size_mm,
                    "colors": {"s0": "p0", "s1": "p1"},
                },
                "placement": {
                    "type": "lattice",
                    "lattice": {
                        "cell_w_mm": cell_mm,
                        "cell_h_mm": cell_mm,
                        "drop_fraction": 0.5,
                        "drop_axis": "row",
                    },
                },
            },
        ],
    }


def test_recraft_motif_composes_into_seamless_tile():
    motif_id = _register_motif()
    # background stripped -> only the two object colors (pink body, dark wheels) remain.
    assert MOTIFS[motif_id].color_slots == ("s0", "s1")

    intent = _build_tile_intent(motif_id, tile_mm=48.0, cell_mm=24.0, size_mm=18.0)
    svg = generate(intent, seed=7).svg

    root = ET.fromstring(svg)
    uses = root.findall(f".//{NS}use")
    assert len(uses) >= 4  # a real repeated tile (lattice instances + clones), not one object
    assert "<pattern" in svg  # seamless tile uses a <pattern> wrapper
    # every bound palette color is present in the output
    assert "#ef9aa6" in svg and "#2e2a2a" in svg


def test_tile_is_byte_deterministic():
    motif_id = _register_motif()
    intent = _build_tile_intent(motif_id)
    assert generate(intent, seed=7).svg == generate(intent, seed=7).svg


def test_background_color_fills_tile_once_not_baked_per_motif():
    # The motif carries NO background (gate stripped it). The background color therefore
    # appears exactly once — as the single background-layer rect — rather than baked into
    # the motif and repainted at every lattice instance.
    motif_id = _register_motif()
    intent = _build_tile_intent(motif_id, bg_hex="#f5efe3")
    svg = generate(intent, seed=7).svg
    root = ET.fromstring(svg)
    full_bg = [
        r for r in root.findall(f".//{NS}rect")
        if r.get("width") in ("48", "48.0")
        and r.get("height") in ("48", "48.0")
        and r.get("fill") == "#f5efe3"
    ]
    assert len(full_bg) == 1
