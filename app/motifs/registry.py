"""Registry for reusable motif SVG definitions.

Each motif is normalized to the intake contract ``{id, symbol, bbox_mm, anchor}``
that Placement/Composition consume without knowing the inner geometry:

- ``symbol`` is a single ``<symbol id="motif-{id}" overflow="visible">…</symbol>``
  string. It carries no ``viewBox`` so a ``<use transform>`` maps 1:1 in mm and
  stays renderer-portable (no implicit symbol-viewport scaling).
- Geometry is authored in a **normalized unit box**: the nominal bounding box
  spans ``1.0`` and is centered on ``anchor=(0.0, 0.0)``. Composition therefore
  scales an instance by ``scale = size_mm`` (i.e. ``size_mm`` is the rendered
  bounding-box extent in mm) and the anchor lands exactly on the lane point.
- Color is normalized to a slot reference via ``fill="currentColor"``; the bound
  output color is set as ``color=`` on the ``<use>`` at composition time, so
  colorway swaps work. The MVP ``circle``/``bee`` are single-color.

Built-in motifs are registered here. Recraft-generated motifs (session 8) will be
registered through the same contract at authoring time.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from app.engine.units import fmt
from app.render import sanitize

BBox = tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y) in mm
Anchor = tuple[float, float]  # (x, y) in mm


@dataclass(frozen=True)
class MotifDef:
    """Normalized motif intake contract exposed to Placement/Composition."""

    id: str
    symbol: str
    bbox_mm: BBox
    anchor: Anchor


# Nominal unit box: extent 1.0, centered on the anchor at the origin.
_UNIT_BBOX: BBox = (-0.5, -0.5, 0.5, 0.5)
_ORIGIN: Anchor = (0.0, 0.0)


def _symbol(motif_id: str, geometry: str) -> str:
    return f'<symbol id="motif-{motif_id}" overflow="visible">{geometry}</symbol>'


_CIRCLE = MotifDef(
    id="circle",
    symbol=_symbol("circle", '<circle cx="0" cy="0" r="0.5" fill="currentColor"/>'),
    bbox_mm=_UNIT_BBOX,
    anchor=_ORIGIN,
)

# A simple, single-color bee silhouette within the unit box: a vertical body
# ellipse flanked by two wing ellipses. All fills are currentColor.
_BEE = MotifDef(
    id="bee",
    symbol=_symbol(
        "bee",
        '<ellipse cx="0" cy="0" rx="0.22" ry="0.42" fill="currentColor"/>'
        '<ellipse cx="-0.3" cy="-0.1" rx="0.18" ry="0.28" fill="currentColor"/>'
        '<ellipse cx="0.3" cy="-0.1" rx="0.18" ry="0.28" fill="currentColor"/>',
    ),
    bbox_mm=_UNIT_BBOX,
    anchor=_ORIGIN,
)

MOTIFS: dict[str, MotifDef] = {
    _CIRCLE.id: _CIRCLE,
    _BEE.id: _BEE,
}


def get_motif(motif_id: str) -> MotifDef:
    """Look up a registered motif by id.

    Raises ``ValueError`` (with the available ids) for an unknown motif, mirroring
    ``host.resolve_lane``.
    """
    try:
        return MOTIFS[motif_id]
    except KeyError:
        available = ", ".join(sorted(MOTIFS))
        raise ValueError(f"unknown motif {motif_id!r}; available: {available}") from None


def register_motif(motif: MotifDef) -> str:
    """Register a normalized motif under its content-hash id (idempotent).

    Re-registering an identical id is a cache hit (no-op): the same normalized SVG
    always hashes to the same id, so authoring the same shape twice does not diverge.
    Runtime keeps referencing motifs by id only, preserving determinism.
    """
    MOTIFS[motif.id] = motif
    return motif.id


def _parse_viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    """Return the source coordinate frame ``(min_x, min_y, w, h)`` from viewBox or size."""
    vb = root.get("viewBox")
    if vb:
        nums = [float(p) for p in vb.replace(",", " ").split()]
        if len(nums) != 4:
            raise ValueError(f"motif SVG has a malformed viewBox: {vb!r}")
        if nums[2] <= 0 or nums[3] <= 0:
            # Mirror the size-branch guard: a zero/negative extent would divide-by-zero
            # or silently mirror/off-center the geometry (SVG forbids it anyway).
            raise ValueError(f"motif SVG viewBox must have positive width/height: {vb!r}")
        return nums[0], nums[1], nums[2], nums[3]
    w = float(root.get("width", "0") or 0)
    h = float(root.get("height", "0") or 0)
    if w <= 0 or h <= 0:
        raise ValueError("motif SVG needs a viewBox or positive width/height")
    return 0.0, 0.0, w, h


def _recolor_to_slot(el: ET.Element) -> None:
    """Replace concrete fill/stroke colors with the ``currentColor`` slot reference.

    Single-color normalization (matches the built-in motif convention). ``none`` and
    internal ``url(#…)`` paints are left intact. Multi-color slot binding is future work.
    """
    for node in el.iter():
        for attr in ("fill", "stroke"):
            value = node.get(attr)
            if value is None:
                continue
            low = value.strip().lower()
            if low == "none" or low.startswith("url("):
                continue
            node.set(attr, "currentColor")


# Elements that actually paint. A motif whose geometry is only non-rendering
# containers (``<defs>``) or empty would register as an invisible motif.
_RENDERABLE = frozenset(
    {"path", "polygon", "polyline", "rect", "circle", "ellipse", "line", "use"}
)


def _has_drawable(elements: list[ET.Element]) -> bool:
    """True if any renderable element exists outside a ``<defs>`` subtree."""
    for el in elements:
        if el.tag == "defs":
            continue  # non-rendering container
        if el.tag in _RENDERABLE:
            return True
        if _has_drawable(list(el)):
            return True
    return False


def normalize_motif_svg(raw_svg: str) -> MotifDef:
    """Normalize an authored/Recraft SVG into the motif intake contract.

    Steps (ARCHITECTURE.md "Motif 소스와 registry"):

    1. Parse + allowlist via :mod:`app.render.sanitize` — ``<filter>``, embedded
       raster (``<image>``) and external ``href`` are outside the allowlist and so are
       rejected (a motif must be vector).
    2. Map the source ``viewBox`` frame into the normalized unit box (extent ``1.0``,
       centered on the origin) with a single wrapping ``<g transform>`` — no per-path
       coordinate rewriting (pixel-tight bbox flattening is out of scope; the viewBox
       is the authoring frame).
    3. Substitute concrete colors with the ``currentColor`` slot reference
       (single-color; multi-color slot binding is future work).
    4. Wrap in a single ``<symbol>`` and derive a content-hash ``motif_id`` from the
       **normalized** geometry, so the same shape always hashes to the same id (the
       cache-hit guarantee).
    """
    root = sanitize.parse_svg(raw_svg)
    sanitize.validate_tree(root)

    min_x, min_y, w, h = _parse_viewbox(root)
    extent = max(w, h)
    scale = 1.0 / extent
    # Map the source-frame center onto the origin and the longest side onto 1.0.
    tx = -(min_x + w / 2.0) * scale
    ty = -(min_y + h / 2.0) * scale

    children = list(root)
    if not _has_drawable(children):
        raise ValueError("motif SVG has no drawable geometry")
    for child in children:
        _recolor_to_slot(child)
    inner = "".join(ET.tostring(child, encoding="unicode") for child in children)
    geometry = (
        f'<g transform="translate({fmt(tx)} {fmt(ty)}) scale({fmt(scale)})">'
        f"{inner}</g>"
    )

    motif_id = "recraft-" + hashlib.sha256(geometry.encode("utf-8")).hexdigest()[:12]
    return MotifDef(
        id=motif_id,
        symbol=_symbol(motif_id, geometry),
        bbox_mm=_UNIT_BBOX,
        anchor=_ORIGIN,
    )
