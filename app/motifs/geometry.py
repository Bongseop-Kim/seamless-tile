"""Deterministic geometry bounding box for SVG element trees (session 13 follow-up).

Used to (a) tight-frame a motif in ``normalize_motif_svg`` so the actual object — not the
authoring viewBox with its margins — maps to the unit box, and (b) detect a full-canvas
background shape in the Recraft suitability gate. Pure functions of the coordinates only
(no time / randomness / dict ordering), so the determinism contract holds (spec §9).

Bezier/arc bounds use control points / radius boxes — a deterministic *over-estimate*
(never smaller than the true bounds), which only ever yields a slightly looser frame, so
geometry is never clipped.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

# Affine transform as (a, b, c, d, e, f): x' = a*x + c*y + e, y' = b*x + d*y + f (SVG).
Matrix = tuple[float, float, float, float, float, float]
Box = tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# Tags that contribute drawable geometry (others — defs/symbol/use/g — carry no own coords).
SHAPE_TAGS = frozenset({"path", "rect", "circle", "ellipse", "line", "polygon", "polyline"})
DRAWABLE_TAGS = SHAPE_TAGS | {"g"}

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_PATH_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")
_TRANSFORM = re.compile(r"(\w+)\s*\(([^)]*)\)")


def _floats(text: str) -> list[float]:
    return [float(m.group(0)) for m in _NUM.finditer(text or "")]


def _mul(m1: Matrix, m2: Matrix) -> Matrix:
    """Compose so apply(_mul(m1, m2), p) == apply(m1, apply(m2, p))."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _transform_matrix(name: str, v: list[float]) -> Matrix | None:
    if name == "translate":
        return (1.0, 0.0, 0.0, 1.0, v[0] if v else 0.0, v[1] if len(v) > 1 else 0.0)
    if name == "scale":
        sx = v[0] if v else 1.0
        return (sx, 0.0, 0.0, v[1] if len(v) > 1 else sx, 0.0, 0.0)
    if name == "rotate" and v:
        rad = math.radians(v[0])
        cos, sin = math.cos(rad), math.sin(rad)
        rot: Matrix = (cos, sin, -sin, cos, 0.0, 0.0)
        if len(v) >= 3:  # rotate about (cx, cy)
            cx, cy = v[1], v[2]
            return _mul((1.0, 0.0, 0.0, 1.0, cx, cy), _mul(rot, (1.0, 0.0, 0.0, 1.0, -cx, -cy)))
        return rot
    if name == "matrix" and len(v) >= 6:
        return (v[0], v[1], v[2], v[3], v[4], v[5])
    if name == "skewX" and v:
        return (1.0, 0.0, math.tan(math.radians(v[0])), 1.0, 0.0, 0.0)
    if name == "skewY" and v:
        return (1.0, math.tan(math.radians(v[0])), 0.0, 1.0, 0.0, 0.0)
    return None


def parse_transform(value: str | None) -> Matrix:
    if not value:
        return IDENTITY
    m = IDENTITY
    for name, args in _TRANSFORM.findall(value):
        tm = _transform_matrix(name, _floats(args))
        if tm is not None:
            m = _mul(m, tm)
    return m


def _path_points(d: str) -> list[tuple[float, float]]:
    """All on-curve + control points of a path (a safe over-estimate of its bounds)."""
    toks = [
        ("cmd", mt.group(1)) if mt.group(1) else ("num", float(mt.group(2)))
        for mt in _PATH_TOKEN.finditer(d or "")
    ]
    pts: list[tuple[float, float]] = []
    i, n = 0, len(toks)
    cx = cy = sx = sy = 0.0
    cmd: str | None = None
    try:
        while i < n:
            if toks[i][0] == "cmd":
                cmd = toks[i][1]
                i += 1
                if cmd in ("Z", "z"):
                    cx, cy = sx, sy
                    continue
            if cmd is None:
                break
            cl, rel = cmd.lower(), cmd.islower()

            def nxt() -> float:
                nonlocal i
                v = toks[i][1]
                i += 1
                return v  # type: ignore[return-value]

            if cl == "m":
                x, y = nxt(), nxt()
                if rel:
                    x, y = x + cx, y + cy
                cx, cy = sx, sy = x, y
                pts.append((cx, cy))
                cmd = "l" if rel else "L"  # implicit subsequent pairs are lineto
            elif cl == "l":
                x, y = nxt(), nxt()
                if rel:
                    x, y = x + cx, y + cy
                cx, cy = x, y
                pts.append((cx, cy))
            elif cl == "h":
                x = nxt()
                cx = cx + x if rel else x
                pts.append((cx, cy))
            elif cl == "v":
                y = nxt()
                cy = cy + y if rel else y
                pts.append((cx, cy))
            elif cl == "c":
                vals = [nxt() for _ in range(6)]
                if rel:
                    vals = [vals[k] + (cx if k % 2 == 0 else cy) for k in range(6)]
                pts += [(vals[0], vals[1]), (vals[2], vals[3]), (vals[4], vals[5])]
                cx, cy = vals[4], vals[5]
            elif cl == "s" or cl == "q":
                vals = [nxt() for _ in range(4)]
                if rel:
                    vals = [vals[k] + (cx if k % 2 == 0 else cy) for k in range(4)]
                pts += [(vals[0], vals[1]), (vals[2], vals[3])]
                cx, cy = vals[2], vals[3]
            elif cl == "t":
                x, y = nxt(), nxt()
                if rel:
                    x, y = x + cx, y + cy
                pts.append((x, y))
                cx, cy = x, y
            elif cl == "a":
                rx, ry, _rot, _laf, _sf, x, y = (nxt() for _ in range(7))
                if rel:
                    x, y = x + cx, y + cy
                rx, ry = abs(rx), abs(ry)  # radius box around both endpoints (over-estimate)
                pts += [(x, y), (cx + rx, cy + ry), (cx - rx, cy - ry),
                        (x + rx, y + ry), (x - rx, y - ry)]
                cx, cy = x, y
            else:
                i += 1
    except IndexError:
        pass  # malformed/truncated path: bound whatever was parsed
    return pts


def _shape_points(tag: str, el: ET.Element) -> list[tuple[float, float]]:
    def f(name: str, default: float = 0.0) -> float:
        try:
            return float(el.get(name, default))
        except (TypeError, ValueError):
            return default

    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        return [(x, y), (x + w, y + h)]
    if tag == "circle":
        cx, cy, r = f("cx"), f("cy"), f("r")
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if tag == "ellipse":
        cx, cy, rx, ry = f("cx"), f("cy"), f("rx"), f("ry")
        return [(cx - rx, cy - ry), (cx + rx, cy + ry)]
    if tag == "line":
        return [(f("x1"), f("y1")), (f("x2"), f("y2"))]
    if tag in ("polygon", "polyline"):
        nums = _floats(el.get("points", ""))
        return [(nums[k], nums[k + 1]) for k in range(0, len(nums) - 1, 2)]
    if tag == "path":
        return _path_points(el.get("d", ""))
    return []


def _bounds(points: list[tuple[float, float]]) -> Box | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _union(boxes: list[Box]) -> Box | None:
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def element_bbox(el: ET.Element, matrix: Matrix = IDENTITY) -> Box | None:
    """Axis-aligned bounding box of ``el`` (and its descendants) in the coordinate space
    of ``matrix``, honoring ``transform`` on this element and every ancestor."""
    if not isinstance(el.tag, str):
        return None
    m = _mul(matrix, parse_transform(el.get("transform")))
    tag = el.tag.lower()
    boxes: list[Box] = []
    own = _bounds([_apply(m, x, y) for x, y in _shape_points(tag, el)])
    if own is not None:
        boxes.append(own)
    for child in el:
        child_box = element_bbox(child, m)
        if child_box is not None:
            boxes.append(child_box)
    return _union(boxes)


def bbox_of(elements: list[ET.Element]) -> Box | None:
    """Union bounding box of a list of sibling elements (e.g. a motif's top-level nodes)."""
    return _union([b for el in elements if (b := element_bbox(el)) is not None])
