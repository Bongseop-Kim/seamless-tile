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

from dataclasses import dataclass

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
