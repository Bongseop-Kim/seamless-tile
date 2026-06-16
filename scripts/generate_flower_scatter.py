#!/usr/bin/env python3
"""Generate a scattered floral SVG tile with per-object torus wrapping."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


@dataclass(frozen=True)
class Motif:
    href: str
    x: float
    y: float
    radius: float
    scale: float
    rotate: float
    opacity: float = 1.0


TILE_SIZE = 1024


MOTIFS = [
    Motif("#flower-coral", 72, 86, 58, 0.94, -18),
    Motif("#flower-cream", 256, 124, 48, 0.72, 24),
    Motif("#leaf-sprig", 472, 64, 66, 0.88, 146, 0.92),
    Motif("#flower-blue", 725, 142, 54, 0.82, 9),
    Motif("#flower-yellow", 970, 76, 55, 0.9, 32),
    Motif("#leaf-sprig", 136, 288, 66, 0.7, -36, 0.86),
    Motif("#flower-blue", 362, 318, 54, 0.68, 72),
    Motif("#flower-coral", 610, 276, 58, 0.78, -54),
    Motif("#flower-cream", 870, 332, 48, 0.76, 11),
    Motif("#bud", 32, 508, 34, 0.88, 18),
    Motif("#flower-yellow", 214, 552, 55, 0.64, -8),
    Motif("#leaf-sprig", 508, 492, 66, 0.86, 62, 0.9),
    Motif("#flower-blue", 742, 542, 54, 0.74, -20),
    Motif("#flower-coral", 1008, 528, 58, 0.88, 12),
    Motif("#flower-cream", 96, 802, 48, 0.7, -28),
    Motif("#flower-coral", 316, 780, 58, 0.82, 38),
    Motif("#bud", 564, 830, 34, 0.86, -44),
    Motif("#flower-yellow", 770, 780, 55, 0.72, 14),
    Motif("#leaf-sprig", 956, 910, 66, 0.8, -140, 0.88),
    Motif("#flower-blue", 430, 1002, 54, 0.86, -6),
]


def _sub(parent: ET.Element, tag: str, attrs: dict[str, object] | None = None) -> ET.Element:
    return ET.SubElement(parent, f"{{{SVG_NS}}}{tag}", {k: str(v) for k, v in (attrs or {}).items()})


def _add_flower(
    defs: ET.Element,
    motif_id: str,
    *,
    petal: str,
    petal_dark: str,
    center: str,
    center_dark: str,
    petals: int,
) -> None:
    group = _sub(defs, "g", {"id": motif_id})
    for index in range(petals):
        angle = index * (360 / petals)
        fill = petal_dark if index % 2 else petal
        _sub(
            group,
            "ellipse",
            {
                "cx": 0,
                "cy": -28,
                "rx": 15,
                "ry": 34,
                "fill": fill,
                "transform": f"rotate({angle:.3f})",
            },
        )
    _sub(group, "circle", {"cx": 0, "cy": 0, "r": 20, "fill": center})
    _sub(group, "circle", {"cx": -6, "cy": -5, "r": 4, "fill": center_dark, "opacity": 0.65})
    _sub(group, "circle", {"cx": 6, "cy": 4, "r": 3.5, "fill": center_dark, "opacity": 0.55})
    _sub(group, "circle", {"cx": 1, "cy": 8, "r": 3, "fill": center_dark, "opacity": 0.5})


def _add_leaf_sprig(defs: ET.Element) -> None:
    group = _sub(defs, "g", {"id": "leaf-sprig"})
    _sub(group, "path", {"d": "M-46 42 C-18 12 16 -16 48 -44", "fill": "none", "stroke": "#4f7f58", "stroke-width": 6, "stroke-linecap": "round"})
    leaves = [(-32, 24, -34), (-14, 8, 28), (4, -8, -32), (24, -24, 30)]
    for cx, cy, angle in leaves:
        _sub(
            group,
            "ellipse",
            {
                "cx": cx,
                "cy": cy,
                "rx": 13,
                "ry": 27,
                "fill": "#6f9d6b",
                "transform": f"rotate({angle} {cx} {cy})",
            },
        )


def _add_bud(defs: ET.Element) -> None:
    group = _sub(defs, "g", {"id": "bud"})
    _sub(group, "path", {"d": "M-10 36 C-8 10 4 -10 22 -28", "fill": "none", "stroke": "#5f8b5d", "stroke-width": 5, "stroke-linecap": "round"})
    _sub(group, "ellipse", {"cx": 24, "cy": -32, "rx": 16, "ry": 25, "fill": "#d75f6a", "transform": "rotate(28 24 -32)"})
    _sub(group, "ellipse", {"cx": 16, "cy": -24, "rx": 9, "ry": 18, "fill": "#ef8a91", "transform": "rotate(-16 16 -24)"})
    _sub(group, "ellipse", {"cx": -8, "cy": 16, "rx": 11, "ry": 22, "fill": "#7aa36c", "transform": "rotate(-28 -8 16)"})


def _add_defs(root: ET.Element) -> None:
    defs = _sub(root, "defs")
    clip = _sub(defs, "clipPath", {"id": "tile-clip"})
    _sub(clip, "rect", {"x": 0, "y": 0, "width": TILE_SIZE, "height": TILE_SIZE})

    _add_flower(
        defs,
        "flower-coral",
        petal="#ef8a7a",
        petal_dark="#dc6b68",
        center="#f5ca57",
        center_dark="#9d6d24",
        petals=8,
    )
    _add_flower(
        defs,
        "flower-blue",
        petal="#7db7d6",
        petal_dark="#5799bf",
        center="#f1d37a",
        center_dark="#8b6f2d",
        petals=7,
    )
    _add_flower(
        defs,
        "flower-yellow",
        petal="#f0c85a",
        petal_dark="#dfa846",
        center="#8b5e3c",
        center_dark="#4f3825",
        petals=9,
    )
    _add_flower(
        defs,
        "flower-cream",
        petal="#f5e6c9",
        petal_dark="#e8cfaa",
        center="#b76458",
        center_dark="#6a4038",
        petals=6,
    )
    _add_leaf_sprig(defs)
    _add_bud(defs)


def _wrap_offsets(position: float, radius: float, period: float) -> list[float]:
    offsets = [0.0]
    if position - radius < 0:
        offsets.append(period)
    if position + radius > period:
        offsets.append(-period)
    return offsets


def _draw_motif(parent: ET.Element, motif: Motif, *, tile_x: float = 0, tile_y: float = 0) -> None:
    radius = motif.radius * motif.scale
    for dx in _wrap_offsets(motif.x, radius, TILE_SIZE):
        for dy in _wrap_offsets(motif.y, radius, TILE_SIZE):
            x = motif.x + dx + tile_x
            y = motif.y + dy + tile_y
            use = _sub(
                parent,
                "use",
                {
                    "href": motif.href,
                    "transform": f"translate({x:.3f} {y:.3f}) rotate({motif.rotate:.3f}) scale({motif.scale:.3f})",
                    "opacity": motif.opacity,
                },
            )
            use.set(f"{{{XLINK_NS}}}href", motif.href)


def build_tile() -> ET.Element:
    root = ET.Element(
        f"{{{SVG_NS}}}svg",
        {"width": str(TILE_SIZE), "height": str(TILE_SIZE), "viewBox": f"0 0 {TILE_SIZE} {TILE_SIZE}"},
    )
    _add_defs(root)
    _sub(root, "rect", {"x": 0, "y": 0, "width": TILE_SIZE, "height": TILE_SIZE, "fill": "#fbfaf2"})
    _sub(root, "path", {"d": "M0 0H1024V1024H0Z", "fill": "#fbfaf2"})
    layer = _sub(root, "g", {"clip-path": "url(#tile-clip)"})
    for motif in MOTIFS:
        _draw_motif(layer, motif)
    return root


def build_preview() -> ET.Element:
    width = TILE_SIZE * 2
    root = ET.Element(
        f"{{{SVG_NS}}}svg",
        {"width": str(width), "height": str(width), "viewBox": f"0 0 {width} {width}"},
    )
    _add_defs(root)
    _sub(root, "rect", {"x": 0, "y": 0, "width": width, "height": width, "fill": "#fbfaf2"})
    for row in range(2):
        for col in range(2):
            tile_x = col * TILE_SIZE
            tile_y = row * TILE_SIZE
            _sub(root, "rect", {"x": tile_x, "y": tile_y, "width": TILE_SIZE, "height": TILE_SIZE, "fill": "#fbfaf2"})
            layer = _sub(root, "g")
            for motif in MOTIFS:
                _draw_motif(layer, motif, tile_x=tile_x, tile_y=tile_y)
    _sub(root, "path", {"d": f"M{TILE_SIZE} 0V{width}M0 {TILE_SIZE}H{width}", "fill": "none", "stroke": "#000", "stroke-width": 1, "opacity": 0.08})
    return root


def write_svg(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode"),
        encoding="utf-8",
    )


def main() -> None:
    write_svg(Path("examples/flower-scatter-seamless.svg"), build_tile())
    write_svg(Path("examples/flower-scatter-repeat-preview.svg"), build_preview())


if __name__ == "__main__":
    main()
