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
  colorway swaps work. Single-color motifs keep the legacy ``currentColor`` convention.

No motifs ship built-in; every motif is generated (LLM/Recraft) and registered through
this contract at authoring time.
"""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.engine.units import fmt
from app.motifs import facets
from app.motifs import geometry as geom
from app.render import sanitize

if TYPE_CHECKING:
    from app.motifs.store import MotifStore

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y) in mm
Anchor = tuple[float, float]  # (x, y) in mm


@dataclass(frozen=True)
class MotifDef:
    """Normalized motif intake contract exposed to Placement/Composition.

    ``color_slots`` are the motif-local color slots (``s0, s1, …``) in document DFS
    first-appearance order (D15). A single-color motif keeps the legacy convention:
    one slot ``("s0",)`` bound to ``currentColor`` in ``symbol``. A multi-color motif
    stores ``symbol`` with slot *tokens* (``fill="s0"`` …) — colorway-agnostic and used
    only for hashing/storage; composition derives renderable per-slot symbols from it
    (see ``slot_render_symbols``).
    """

    id: str
    symbol: str
    bbox_mm: BBox
    anchor: Anchor
    color_slots: tuple[str, ...] = ("s0",)


# Nominal unit box: extent 1.0, centered on the anchor at the origin.
_UNIT_BBOX: BBox = (-0.5, -0.5, 0.5, 0.5)
_ORIGIN: Anchor = (0.0, 0.0)

# Tier1 structural-heuristic defaults (spec §8/§12). Callers override from Settings;
# the module-level defaults keep ``normalize_motif_svg`` a pure function of the SVG.
_MAX_ASPECT_RATIO = 20.0  # reject bbox with longest/shortest side beyond this
_EDGE_SEAM_TOL = 2.0  # per-channel mean edge_seam tolerance (00-overview <= 2.0)
# Render-gate tile: fixed mm + DPI so the rasterized size is deterministic, and a 10%
# transparent margin so a motif that fills its unit box is not a false positive (only
# geometry overflowing its declared bbox — e.g. stroke width geom.bbox_of cannot
# measure — reaches the border and trips edge_seam).
_GATE_RENDER_MM = 10.0
_GATE_RENDER_DPI = 300
_GATE_MARGIN_FRAC = 0.1


def _render_structure_gate(motif: MotifDef, *, edge_seam_tol: float) -> None:
    """Render-based Tier1 checks (spec §8): reject a motif that fails to render (#4) or
    whose rendered geometry overflows its declared unit box (#5).

    Best-effort: when no SVG renderer is installed this is a no-op, so the gate degrades
    gracefully (librsvg is not a hard dependency). The motif is rendered into a tile with
    a transparent margin around its unit box; a well-contained motif leaves the border
    transparent (``edge_seam`` ~ 0), while geometry bleeding past the declared bbox (e.g.
    stroke width, which ``geom.bbox_of`` does not measure) reaches the border and trips
    ``edge_seam``. This guards bbox overflow — real tiling seamlessness is the engine's
    by-construction guarantee, not a motif-level property. This decides accept/reject
    only; it never mutates the motif, so generated SVG bytes stay deterministic.
    """
    import io

    import numpy as np
    from PIL import Image

    from app.render.raster import RasterError, find_renderer, rasterize
    from app.render.svg import render_svg_document
    from app.validate.seamless import edge_seam

    binary = find_renderer()
    if not binary:
        return  # no renderer: skip render-dependent checks (best-effort)

    size = float(_GATE_RENDER_MM)
    scale = size * (1.0 - 2.0 * _GATE_MARGIN_FRAC)  # unit box -> centered, margined
    transform = f"translate({fmt(size / 2.0)} {fmt(size / 2.0)}) scale({fmt(scale)})"
    symbols = slot_render_symbols(motif)
    defs = "".join(symbol for _, symbol in symbols)
    body = "".join(
        f'<use href="#{sym_id}" color="#000000" transform="{transform}"/>'
        for sym_id, _ in symbols
    )
    document = render_svg_document(body, size, defs=defs)

    try:
        png, _ = rasterize(document, "png", _GATE_RENDER_DPI, size, binary=binary)
    except RasterError as exc:
        raise ValueError(f"motif failed to render: {exc}") from exc

    arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))
    seam_x, seam_y = edge_seam(arr)
    if max(seam_x, seam_y) > edge_seam_tol:
        raise ValueError(
            f"motif geometry overflows its declared bbox "
            f"(edge_seam {max(seam_x, seam_y):.2f} > {edge_seam_tol})"
        )


def _symbol(motif_id: str, geometry: str) -> str:
    return f'<symbol id="motif-{motif_id}" overflow="visible">{geometry}</symbol>'


MOTIFS: dict[str, MotifDef] = {}


# No motifs ship built-in (kept as an empty set so consumers — e.g. test cleanup and the
# delete_motif guard — can still distinguish built-ins from dynamically registered motifs).
BUILTIN_MOTIF_IDS: frozenset[str] = frozenset(MOTIFS)


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
    scope: str | None = None,
    view: str | None = None,
    expression: str | None = None,
    style: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    source: str = "recraft",
    color_slots: list[str] | None = None,
    embedding: list[float] | None = None,
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
    signature. ``subject`` is free text; ``scope`` is the one controlled facet (D10).
    ``embedding`` (S11, D12) is the descriptor vector persisted alongside the facets so
    future requests can soft-match this motif.
    """
    # Validate facets up front: an out-of-vocab value is a caller bug and must
    # propagate, unlike a DB outage (swallowed in _write_through). Keeping this out of
    # the persistence path also decouples validation from whether a store is configured.
    facets.validate_facets(scope)
    _write_through(
        motif,
        subject=subject,
        scope=scope,
        view=view,
        expression=expression,
        style=style,
        description=description,
        tags=tags or [],
        source=source,
        color_slots=color_slots or list(motif.color_slots),
        embedding=embedding,
    )
    MOTIFS[motif.id] = motif
    _bump_registry_pool_epoch()
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
    variant_group = facets.variant_group_key(
        facet_kwargs.get("subject"), facet_kwargs.get("scope")
    )
    record = MotifRecord(
        id=motif.id,
        symbol=motif.symbol,
        bbox_mm=motif.bbox_mm,
        anchor=motif.anchor,
        variant_group=variant_group,
        **facet_kwargs,
    )
    try:
        store.upsert(record)  # ON CONFLICT DO NOTHING => idempotent
    except Exception:  # DB failure is non-fatal at authoring time
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


def delete_motif(motif_id: str) -> None:
    """Delete a motif, propagating removal across DB, in-memory and caches (spec §6.4).

    Keeps the three layers consistent: the store row is deleted, the in-memory
    ``MOTIFS`` entry is evicted, and the adapter caches that map a spec/prompt to a
    motif id are flushed, so a deleted motif can never be re-served from a warm cache.
    A full flush is deliberate — admin deletion is rare, so dropping warm caches is
    cheaper and safer than maintaining per-id reverse indices. The intent and embedding
    caches hold no concrete motif id, so they are left intact. The flush is process-local
    (the caches are module-level globals): a separately running server keeps its own
    caches and must be restarted after a delete.

    Built-in motifs are code constants, not catalog rows, and cannot be deleted.
    """
    if motif_id in BUILTIN_MOTIF_IDS:
        raise ValueError(f"cannot delete built-in motif {motif_id!r}")
    from app.motifs.store import _resolve_store

    _resolve_store().delete(motif_id)
    MOTIFS.pop(motif_id, None)
    _flush_motif_id_caches()
    _bump_registry_pool_epoch()


def _flush_motif_id_caches() -> None:
    """Flush adapter caches whose values are motif ids (spec §6.4 cache invalidation)."""
    # Lazy import: the adapters import this module, so a top-level import is a cycle.
    from app.adapters import llm, recraft

    llm.clear_motif_svg_cache()
    recraft.clear_motif_cache()
    recraft.clear_recraft_motif_cache()


# Monotonic counter bumped whenever the reusable motif pool changes. Lets
# `adapters.registry_fingerprint.registry_version_for` memoize its per-request pool
# fingerprint instead of querying the store on every generate request. Process-local,
# the same scope as the cache flush in `delete_motif` (a separately running server keeps
# its own counter; mutations are expected to go through this module's register/delete API).
_registry_pool_epoch = 0


def registry_pool_epoch() -> int:
    """Current reusable-pool epoch (bumped on every pool mutation)."""
    return _registry_pool_epoch


def _bump_registry_pool_epoch() -> None:
    global _registry_pool_epoch
    _registry_pool_epoch += 1


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


def _norm_color(value: str) -> str | None:
    """Normalized comparison key for a concrete paint value, or ``None`` for a
    non-color paint (``none`` / internal ``url(#…)``) that carries no slot.

    ``currentColor`` is intentionally treated as a concrete value: a pure-``currentColor``
    motif stays single-color (1 distinct -> legacy path), but when mixed with concrete
    colors it is promoted to its own slot token (multicolor motifs bind every color
    explicitly — no implicit inherited paint survives into a per-slot symbol).
    """
    low = value.strip().lower()
    if low == "none" or low.startswith("url("):
        return None
    return low


def _distinct_colors(children: list[ET.Element]) -> list[str]:
    """Distinct concrete paint values across ``children`` in document DFS
    first-appearance order (deterministic slot ordering, D15)."""
    order: list[str] = []
    for child in children:
        for node in child.iter():
            for attr in ("fill", "stroke"):
                value = node.get(attr)
                if value is None:
                    continue
                norm = _norm_color(value)
                if norm is not None and norm not in order:
                    order.append(norm)
    return order


def _recolor_single(children: list[ET.Element]) -> None:
    """Legacy single-color normalization: every concrete fill/stroke -> ``currentColor``
    (``none`` / internal ``url(#…)`` left intact). Byte-identical to the pre-multicolor
    behavior, preserving single-color motif ids and rendered output."""
    for child in children:
        for node in child.iter():
            for attr in ("fill", "stroke"):
                value = node.get(attr)
                if value is None:
                    continue
                if _norm_color(value) is None:
                    continue
                node.set(attr, "currentColor")


def _slotize_colors(children: list[ET.Element]) -> tuple[str, ...]:
    """Replace concrete fill/stroke colors with motif-local slot tokens and return the
    ordered ``color_slots`` (D15).

    A motif with <=1 distinct concrete color keeps the legacy single-color convention
    (``currentColor`` + ``("s0",)``) so its id and rendered output are unchanged. A
    multi-color motif maps each distinct color (DFS first-appearance order) to a token
    ``s0, s1, …`` written as the attribute value; the colorway-agnostic ``<symbol>`` is
    expanded to renderable per-slot symbols at composition time (``slot_render_symbols``).
    """
    order = _distinct_colors(children)
    if len(order) <= 1:
        _recolor_single(children)
        return ("s0",)
    token = {color: f"s{i}" for i, color in enumerate(order)}
    for child in children:
        for node in child.iter():
            for attr in ("fill", "stroke"):
                value = node.get(attr)
                if value is None:
                    continue
                norm = _norm_color(value)
                if norm is None:
                    continue
                node.set(attr, token[norm])
    return tuple(f"s{i}" for i in range(len(order)))


def _hex_to_rgb(color: str) -> tuple[int, int, int] | None:
    """Parse a ``#rgb`` / ``#rrggbb`` token to an ``(r, g, b)`` tuple, else ``None``."""
    c = color.strip().lower()
    if not c.startswith("#"):
        return None
    h = c[1:]
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _quantize_colors(children: list[ET.Element], max_slots: int) -> None:
    """Deterministically merge concrete colors down to at most ``max_slots`` (§6.2/§12).

    Painterly Recraft output can carry more colors than the slot budget. Merging is a
    pure function of the colors: repeatedly fuse the two closest hex colors (RGB
    Euclidean distance; ties broken by hex order, keeping the lexicographically smaller
    hex as the representative) until the budget is met. Non-hex paints (e.g.
    ``currentColor``) cannot be measured and are never merged; if the irreducible count
    still exceeds ``max_slots`` the motif is rejected (``ValueError``) so the caller can
    regenerate / fall back. Mutates ``children`` in place; ``_slotize_colors`` runs next.
    """
    distinct = _distinct_colors(children)
    if len(distinct) <= max_slots:
        return
    rgb = {c: _hex_to_rgb(c) for c in distinct}
    rep = {c: c for c in distinct}  # original color -> current representative
    unmergeable = sum(1 for c in distinct if rgb[c] is None)
    active = sorted(c for c in distinct if rgb[c] is not None)
    while unmergeable + len(active) > max_slots and len(active) >= 2:
        best: tuple[int, str, str] | None = None  # (distance, keep_hex, drop_hex)
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                a, b = active[i], active[j]  # a < b (active is sorted)
                ra, rb = rgb[a], rgb[b]
                dist = (ra[0] - rb[0]) ** 2 + (ra[1] - rb[1]) ** 2 + (ra[2] - rb[2]) ** 2
                cand = (dist, a, b)
                if best is None or cand < best:
                    best = cand
        _, keep, drop = best
        for color, representative in rep.items():
            if representative == drop:
                rep[color] = keep
        active.remove(drop)
    if unmergeable + len(active) > max_slots:
        raise ValueError(
            f"motif has {len(distinct)} colors that cannot be quantized to "
            f"{max_slots} slots (too many non-hex paints)"
        )
    for child in children:
        for node in child.iter():
            for attr in ("fill", "stroke"):
                value = node.get(attr)
                if value is None:
                    continue
                norm = _norm_color(value)
                if norm is not None and rep.get(norm, norm) != norm:
                    node.set(attr, rep[norm])


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


def normalize_motif_svg(
    raw_svg: str,
    *,
    max_color_slots: int | None = None,
    max_aspect_ratio: float = _MAX_ASPECT_RATIO,
    edge_seam_tol: float = _EDGE_SEAM_TOL,
    render_check: bool = True,
) -> MotifDef:
    """Normalize an authored/Recraft SVG into the motif intake contract.

    Steps (ARCHITECTURE.md "Motif 소스와 registry"):

    1. Parse + allowlist via :mod:`app.render.sanitize` — ``<filter>``, embedded
       raster (``<image>``) and external ``href`` are outside the allowlist and so are
       rejected (a motif must be vector).
    2. Frame on the **tight bounding box of the actual geometry** (not the source
       viewBox): map that bbox's center onto the origin and its longest side onto ``1.0``
       with a single wrapping ``<g transform>``. This makes the object *fill* the unit
       box so ``size_mm`` controls its real rendered size (the source viewBox, which may
       leave wide margins around a small object, is only validated for sanity). A motif
       is a single object placed/repeated by the engine — not a pre-composed scene.
    3. Map concrete colors to motif-local slots (D15): a single-color motif keeps the
       legacy ``currentColor`` convention (``color_slots=("s0",)``); a multi-color motif
       writes slot tokens (``fill="s0"`` …) in document DFS first-appearance order.
    4. Wrap in a single ``<symbol>`` and derive a content-hash ``motif_id`` from the
       **normalized, slotified** geometry, so the same shape always hashes to the same
       id regardless of colorway (the cache-hit guarantee).

    Structural heuristics (spec §8) reject a motif that has no drawable geometry, a
    zero/degenerate extent, a bbox aspect ratio beyond ``max_aspect_ratio``, or — when
    ``render_check`` is set and an SVG renderer is installed — fails to render or whose
    rendered geometry overflows its declared unit box (``edge_seam`` over a margined
    tile exceeds ``edge_seam_tol``). The render-based checks are best-effort: with no
    renderer they are skipped, so the function stays usable without librsvg.
    """
    root = sanitize.parse_svg(raw_svg)
    sanitize.validate_tree(root)

    _parse_viewbox(root)  # validate the source frame (rejects missing / non-positive viewBox)

    children = list(root)
    if not _has_drawable(children):
        raise ValueError("motif SVG has no drawable geometry")

    bbox = geom.bbox_of(children)
    if bbox is None:
        raise ValueError("motif SVG has no measurable geometry")
    bx, by, bx2, by2 = bbox
    bw, bh = bx2 - bx, by2 - by
    extent = max(bw, bh)
    if extent <= 0:
        raise ValueError("motif SVG geometry has zero extent")
    min_side = min(bw, bh)
    if min_side <= 0:
        raise ValueError("motif SVG geometry is degenerate (a zero-width axis)")
    if extent / min_side > max_aspect_ratio:
        raise ValueError(
            f"motif SVG bbox aspect ratio {extent / min_side:.1f} exceeds max "
            f"{max_aspect_ratio} (too thin/elongated)"
        )
    scale = 1.0 / extent
    # Map the tight-geometry center onto the origin and the longest side onto 1.0.
    tx = -(bx + bw / 2.0) * scale
    ty = -(by + bh / 2.0) * scale

    if max_color_slots is not None:
        _quantize_colors(children, max_color_slots)
    color_slots = _slotize_colors(children)
    inner = "".join(ET.tostring(child, encoding="unicode") for child in children)
    geometry = (
        f'<g transform="translate({fmt(tx)} {fmt(ty)}) scale({fmt(scale)})">'
        f"{inner}</g>"
    )

    motif_id = "recraft-" + hashlib.sha256(geometry.encode("utf-8")).hexdigest()[:12]
    motif = MotifDef(
        id=motif_id,
        symbol=_symbol(motif_id, geometry),
        bbox_mm=_UNIT_BBOX,
        anchor=_ORIGIN,
        color_slots=color_slots,
    )
    if render_check:
        _render_structure_gate(motif, edge_seam_tol=edge_seam_tol)
    return motif


def slot_render_symbols(motif: MotifDef) -> list[tuple[str, str]]:
    """Per-slot, colorway-agnostic ``<symbol>`` definitions for composition (D15).

    A single-color motif renders through its original ``currentColor`` symbol unchanged
    (id ``motif-{id}``), preserving the legacy single-color output byte-for-byte.

    A multi-color motif expands to one symbol per slot (id ``motif-{id}-s{k}``): the
    active slot's token becomes ``currentColor`` and every other slot's token becomes
    ``none``. The concrete color is bound per ``<use color>`` instance, so each symbol
    stays colorway-agnostic and dedupes by id. Returned in ``color_slots`` order.

    Token substitution is exact-match on ``fill="s{k}"`` / ``stroke="s{k}"`` (the closing
    quote prevents an ``s1`` vs ``s10`` substring collision) over the symbol string this
    module itself emits, so no re-parse is needed and the result is deterministic.
    """
    if len(motif.color_slots) <= 1:
        return [(f"motif-{motif.id}", motif.symbol)]
    out: list[tuple[str, str]] = []
    for k in range(len(motif.color_slots)):
        body = motif.symbol
        for j, slot in enumerate(motif.color_slots):
            repl = "currentColor" if j == k else "none"
            body = body.replace(f'fill="{slot}"', f'fill="{repl}"')
            body = body.replace(f'stroke="{slot}"', f'stroke="{repl}"')
        sym_id = f"motif-{motif.id}-s{k}"
        body = body.replace(f'id="motif-{motif.id}"', f'id="{sym_id}"', 1)
        out.append((sym_id, body))
    return out
