#!/usr/bin/env python3
"""Wrap an existing SVG motif in a periodic 3x3 seamless-tile layout."""

from __future__ import annotations

import argparse
import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    raw_viewbox = root.get("viewBox")
    if raw_viewbox:
        parts = [float(part) for part in re.split(r"[,\s]+", raw_viewbox.strip()) if part]
        if len(parts) == 4:
            return tuple(parts)  # type: ignore[return-value]

    width = _parse_length(root.get("width"))
    height = _parse_length(root.get("height"))
    if width is None or height is None:
        raise ValueError("SVG must have either viewBox or numeric width/height.")
    return 0.0, 0.0, width, height


def _parse_length(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.match(r"^\s*([0-9]*\.?[0-9]+)", value)
    return float(match.group(1)) if match else None


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _is_full_tile_background(
    child: ET.Element,
    min_x: float,
    min_y: float,
    width: float,
    height: float,
) -> bool:
    tag = _local_name(child.tag)
    if tag == "rect":
        return (
            float(child.get("x", min_x)) == min_x
            and float(child.get("y", min_y)) == min_y
            and float(child.get("width", width)) == width
            and float(child.get("height", height)) == height
        )

    if tag != "path":
        return False

    d = re.sub(r"\s+", "", child.get("d", "")).upper()
    return d == (
        f"M{_fmt(min_x)}{_fmt(min_y)}"
        f"L{_fmt(min_x + width)}{_fmt(min_y)}"
        f"L{_fmt(min_x + width)}{_fmt(min_y + height)}"
        f"L{_fmt(min_x)}{_fmt(min_y + height)}"
        f"L{_fmt(min_x)}{_fmt(min_y)}Z"
    ).upper()


def wrap_svg(
    source: Path,
    target: Path,
    *,
    shift_x: float = 0.0,
    shift_y: float = 0.0,
) -> None:
    root = ET.parse(source).getroot()
    min_x, min_y, width, height = _parse_viewbox(root)

    output = ET.Element(
        f"{{{SVG_NS}}}svg",
        {
            "width": root.get("width", _fmt(width)),
            "height": root.get("height", _fmt(height)),
            "viewBox": " ".join(_fmt(part) for part in (min_x, min_y, width, height)),
        },
    )

    defs = ET.SubElement(output, f"{{{SVG_NS}}}defs")
    clip = ET.SubElement(defs, f"{{{SVG_NS}}}clipPath", {"id": "periodic-tile-clip"})
    ET.SubElement(
        clip,
        f"{{{SVG_NS}}}rect",
        {"x": _fmt(min_x), "y": _fmt(min_y), "width": _fmt(width), "height": _fmt(height)},
    )

    motif = ET.SubElement(defs, f"{{{SVG_NS}}}g", {"id": "periodic-tile-motif"})
    background: ET.Element | None = None

    for child in list(root):
        if _local_name(child.tag) == "defs":
            for original_def in list(child):
                defs.append(copy.deepcopy(original_def))
            continue

        if background is None and _is_full_tile_background(child, min_x, min_y, width, height):
            background = copy.deepcopy(child)
            continue

        motif.append(copy.deepcopy(child))

    if background is not None:
        output.append(background)

    repeated = ET.SubElement(output, f"{{{SVG_NS}}}g", {"clip-path": "url(#periodic-tile-clip)"})
    for y_offset in (-height, 0.0, height):
        for x_offset in (-width, 0.0, width):
            transform = f"translate({_fmt(x_offset + shift_x)} {_fmt(y_offset + shift_y)})"
            use = ET.SubElement(
                repeated,
                f"{{{SVG_NS}}}use",
                {"transform": transform, "href": "#periodic-tile-motif"},
            )
            use.set(f"{{{XLINK_NS}}}href", "#periodic-tile-motif")

    target.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(output, encoding="unicode"),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--shift-x", type=float, default=0.0)
    parser.add_argument("--shift-y", type=float, default=0.0)
    args = parser.parse_args()

    wrap_svg(args.source, args.target, shift_x=args.shift_x, shift_y=args.shift_y)


if __name__ == "__main__":
    main()
