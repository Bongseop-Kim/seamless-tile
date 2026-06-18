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
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.engine.units import fmt
from app.motifs import facets
from app.render import sanitize

if TYPE_CHECKING:
    from app.motifs.store import MotifStore

logger = logging.getLogger(__name__)

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

    Fast path is a pure in-memory dict lookup (sync, no DB) — this keeps the engine
    compose hot path deterministic. On a cold miss, if a store is configured, try to
    lazy-load the motif once and cache it; otherwise raise ``ValueError`` (with the
    available ids) exactly as before, mirroring ``host.resolve_lane``.
    """
    motif = MOTIFS.get(motif_id)
    if motif is not None:
        return motif
    loaded = _lazy_load(motif_id)
    if loaded is not None:
        MOTIFS[motif_id] = loaded
        return loaded
    available = ", ".join(sorted(MOTIFS))
    raise ValueError(f"unknown motif {motif_id!r}; available: {available}") from None


def register_motif(
    motif: MotifDef,
    *,
    subject: str | None = None,
    part: str | None = None,
    view: str | None = None,
    expression: str | None = None,
    style: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    source: str = "recraft",
    color_slots: list[str] | None = None,
) -> str:
    """Register a normalized motif under its content-hash id (idempotent).

    The in-memory ``MOTIFS`` dict stays the source of truth; persistence is a
    best-effort write-through that no-ops when no store is configured and **never**
    raises into the caller (a DB outage must not break authoring or runtime — the
    motif is already usable this process, and the content-hash PK makes a later retry
    idempotent; spec §6.4).

    Re-registering an identical id is a cache hit (no-op): the same normalized SVG
    always hashes to the same id, so authoring the same shape twice does not diverge.
    The optional facet kwargs are inert for current callers; they let the
    motif-resolution glue (S11+) persist semantic metadata without changing this
    signature.
    """
    # Validate facets up front: an out-of-vocab value is a caller bug and must
    # propagate, unlike a DB outage (swallowed in _write_through). Keeping this out of
    # the persistence path also decouples validation from whether a store is configured.
    facets.validate_facets(subject, part)
    MOTIFS[motif.id] = motif
    _write_through(
        motif,
        subject=subject,
        part=part,
        view=view,
        expression=expression,
        style=style,
        description=description,
        tags=tags or [],
        source=source,
        color_slots=color_slots or ["s0"],
    )
    return motif.id


def _write_through(motif: MotifDef, **facet_kwargs) -> None:
    """Persist a registered motif to the configured store (best-effort, non-fatal).

    Facets are already validated by ``register_motif``; here only the DB write happens,
    and its failures are swallowed (a DB outage must not break authoring — the motif is
    usable in-process and the content-hash PK makes a later retry idempotent; §6.4).
    """
    # Imported lazily: store imports MotifDef from this module, so a top-level import
    # here would be a cycle.
    from app.motifs.store import MotifRecord, get_default_store

    store = get_default_store()
    if store is None:
        return  # graceful: unconfigured persistence is a no-op
    try:
        variant_group = facets.variant_group_key(
            facet_kwargs.get("subject"), facet_kwargs.get("part")
        )
        record = MotifRecord(
            id=motif.id,
            symbol=motif.symbol,
            bbox_mm=motif.bbox_mm,
            anchor=motif.anchor,
            variant_group=variant_group,
            **facet_kwargs,
        )
        store.upsert(record)  # ON CONFLICT DO NOTHING => idempotent
    except Exception:  # validation / DB failure is non-fatal at authoring time
        logger.warning("motif write-through failed for %s", motif.id, exc_info=True)


def _lazy_load(motif_id: str) -> MotifDef | None:
    """Try the configured store once for a missing motif. Returns ``None`` when the
    store is unconfigured or the read fails (treated as a graceful miss)."""
    from app.motifs.store import get_default_store

    store = get_default_store()
    if store is None:
        return None
    try:
        record = store.get(motif_id)
    except Exception:
        logger.warning("motif lazy-load failed for %s", motif_id, exc_info=True)
        return None
    return record.to_motif_def() if record is not None else None


def hydrate_from_store(store: "MotifStore") -> int:
    """Load persisted motifs into ``MOTIFS`` at boot (called from the app lifespan).

    Built-in motifs are code constants and are NOT overwritten (``setdefault``); DB
    rows are additive, so re-hydration is idempotent. Returns the row count loaded.
    """
    records = store.all()
    for rec in records:
        MOTIFS.setdefault(rec.id, rec.to_motif_def())
    return len(records)


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
