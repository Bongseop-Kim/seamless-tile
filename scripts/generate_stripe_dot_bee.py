#!/usr/bin/env python3
"""Generate a diagonal stripe + dot motif tile inspired by woven necktie fabric."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


TILE = 1024
PERIOD_U = 256
DOT_PERIOD_V = 32
BEE_PERIOD_U = 512
BEE_PERIOD_V = 256
STRIPE_WIDTH_U = 104


def _sub(parent: ET.Element, tag: str, attrs: dict[str, object] | None = None) -> ET.Element:
    return ET.SubElement(parent, f"{{{SVG_NS}}}{tag}", {key: str(value) for key, value in (attrs or {}).items()})


def _uv_to_xy(u: float, v: float) -> tuple[float, float]:
    return (u + v) / 2, (u - v) / 2


def _line_points(u: float, size: int, margin: float = 360) -> tuple[float, float, float, float]:
    v_min = -size - margin
    v_max = size + margin
    x1, y1 = _uv_to_xy(u, v_min)
    x2, y2 = _uv_to_xy(u, v_max)
    return x1, y1, x2, y2


def _add_bee(defs: ET.Element) -> None:
    bee = _sub(defs, "g", {"id": "gold-bee"})
    _sub(bee, "ellipse", {"cx": -18, "cy": -12, "rx": 17, "ry": 9, "fill": "#d9b85f", "transform": "rotate(-28 -18 -12)", "opacity": 0.9})
    _sub(bee, "ellipse", {"cx": 18, "cy": -12, "rx": 17, "ry": 9, "fill": "#d9b85f", "transform": "rotate(28 18 -12)", "opacity": 0.9})
    _sub(bee, "ellipse", {"cx": -13, "cy": 2, "rx": 13, "ry": 7, "fill": "#cfa84d", "transform": "rotate(-12 -13 2)"})
    _sub(bee, "ellipse", {"cx": 13, "cy": 2, "rx": 13, "ry": 7, "fill": "#cfa84d", "transform": "rotate(12 13 2)"})
    _sub(bee, "ellipse", {"cx": 0, "cy": 4, "rx": 12, "ry": 25, "fill": "#dfbe66"})
    for y in (-9, 1, 11):
        _sub(bee, "path", {"d": f"M-8 {y} C-2 {y + 5} 4 {y + 5} 10 {y}", "fill": "none", "stroke": "#8f6d2e", "stroke-width": 3.2, "stroke-linecap": "round"})
    _sub(bee, "circle", {"cx": 0, "cy": -23, "r": 8, "fill": "#caa04a"})
    _sub(bee, "path", {"d": "M-6 -30 L-13 -39 M6 -30 L13 -39", "fill": "none", "stroke": "#caa04a", "stroke-width": 3, "stroke-linecap": "round"})
    _sub(bee, "path", {"d": "M-18 24 C-26 34 -30 39 -36 45 M18 24 C26 34 30 39 36 45", "fill": "none", "stroke": "#caa04a", "stroke-width": 3, "stroke-linecap": "round"})


def _add_defs(root: ET.Element) -> None:
    defs = _sub(root, "defs")
    _add_bee(defs)
    clip = _sub(defs, "clipPath", {"id": "tile-clip"})
    _sub(clip, "rect", {"x": 0, "y": 0, "width": TILE, "height": TILE})


def _add_base_texture(parent: ET.Element, size: int) -> None:
    texture = _sub(parent, "g", {"opacity": 0.9})
    for y in range(-32, size + 64, 32):
        for x in range(-32, size + 64, 32):
            shift = 16 if (y // 32) % 2 else 0
            cx = x + shift
            color = "#1e416b" if (x // 32 + y // 32) % 3 else "#315981"
            _sub(
                texture,
                "ellipse",
                {
                    "cx": cx,
                    "cy": y,
                    "rx": 10,
                    "ry": 5,
                    "fill": color,
                    "stroke": "#02070d",
                    "stroke-width": 2,
                    "transform": f"rotate(-35 {cx} {y})",
                    "opacity": 0.82,
                },
            )


def _add_diagonal_stripes(parent: ET.Element, size: int) -> None:
    stripes = _sub(parent, "g")
    stroke_width = STRIPE_WIDTH_U / math.sqrt(2)
    for u in range(-size * 2, size * 4, PERIOD_U):
        x1, y1, x2, y2 = _line_points(u, size)
        _sub(stripes, "line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "stroke": "#07111e", "stroke-width": stroke_width + 20, "stroke-linecap": "butt", "opacity": 0.96})
        _sub(stripes, "line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "stroke": "#10243a", "stroke-width": stroke_width, "stroke-linecap": "butt", "opacity": 0.98})

    weave = _sub(parent, "g", {"opacity": 0.58})
    for u in range(-size * 2, size * 4, PERIOD_U):
        for v in range(-size * 2, size * 2, 38):
            x, y = _uv_to_xy(u, v)
            if -40 <= x <= size + 40 and -40 <= y <= size + 40:
                _sub(weave, "ellipse", {"cx": x, "cy": y, "rx": 6, "ry": 12, "fill": "#203d61", "stroke": "#02070d", "stroke-width": 1.6, "transform": f"rotate(-45 {x} {y})"})


def _add_red_dotted_edges(parent: ET.Element, size: int) -> None:
    dots = _sub(parent, "g")
    edge_offsets = (-STRIPE_WIDTH_U / 2, STRIPE_WIDTH_U / 2)
    for u in range(-size * 2, size * 4, PERIOD_U):
        for edge in edge_offsets:
            edge_u = u + edge
            x1, y1, x2, y2 = _line_points(edge_u, size)
            _sub(dots, "line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "stroke": "#05080d", "stroke-width": 15, "stroke-linecap": "butt", "opacity": 0.78})
            for v in range(-size * 2, size * 2, DOT_PERIOD_V):
                x, y = _uv_to_xy(edge_u, v)
                if -18 <= x <= size + 18 and -18 <= y <= size + 18:
                    _sub(dots, "rect", {"x": x - 5, "y": y - 5, "width": 10, "height": 10, "rx": 2, "fill": "#e13026", "stroke": "#590d0a", "stroke-width": 1.2, "transform": f"rotate(-45 {x} {y})"})


def _add_bees(parent: ET.Element, size: int) -> None:
    bees = _sub(parent, "g")
    for u in range(-size * 2, size * 4, BEE_PERIOD_U):
        row = (u // BEE_PERIOD_U) % 2
        for v in range(-size * 2, size * 2, BEE_PERIOD_V):
            x, y = _uv_to_xy(u, v + row * 128)
            if -70 <= x <= size + 70 and -70 <= y <= size + 70:
                use = _sub(
                    bees,
                    "use",
                    {
                        "href": "#gold-bee",
                        "transform": f"translate({x:.3f} {y:.3f}) rotate(-45) scale(0.86)",
                    },
                )
                use.set(f"{{{XLINK_NS}}}href", "#gold-bee")


def _build_scene(size: int, *, show_guides: bool) -> ET.Element:
    root = ET.Element(
        f"{{{SVG_NS}}}svg",
        {"width": str(size), "height": str(size), "viewBox": f"0 0 {size} {size}"},
    )
    _add_defs(root)
    _sub(root, "rect", {"x": 0, "y": 0, "width": size, "height": size, "fill": "#07101b"})
    _add_base_texture(root, size)
    _add_diagonal_stripes(root, size)
    _add_red_dotted_edges(root, size)
    _add_bees(root, size)
    if show_guides:
        for pos in range(TILE, size, TILE):
            _sub(root, "path", {"d": f"M{pos} 0V{size}M0 {pos}H{size}", "fill": "none", "stroke": "#ffffff", "stroke-width": 1, "opacity": 0.14})
    return root


def _write_svg(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode"),
        encoding="utf-8",
    )


def main() -> None:
    _write_svg(Path("examples/stripe-dot-bee-seamless.svg"), _build_scene(TILE, show_guides=False))
    _write_svg(Path("examples/stripe-dot-bee-repeat-preview.svg"), _build_scene(TILE * 2, show_guides=True))


if __name__ == "__main__":
    main()
