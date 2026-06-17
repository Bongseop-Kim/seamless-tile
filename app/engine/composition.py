"""Layer composition engine entrypoint.

Assembles validated layers into a single SVG document using the fixed output
topology: motif geometry defined once in a ``<symbol>`` and instanced with
``<use>``; the whole tile defined as a ``<pattern patternUnits="userSpaceOnUse">``.
Instances are never enumerated as raw geometry (regression guard).
"""

from __future__ import annotations

from app.engine.determinism import sorted_layers
from app.engine.intent import Intent, Layer, MotifLayer
from app.engine.palette import Palette
from app.engine.placement import Instance, place
from app.engine.primitives import build_primitive
from app.engine.units import fmt
from app.motifs.registry import MotifDef, get_motif
from app.render.svg import escape_attr, render_svg_document


def compose(intent: Intent, palette: Palette, colorway_id: str | None = None) -> str:
    """Compose an intent into a single ``<pattern>``-based SVG document."""
    tile = intent.canvas.tile_mm
    layers = sorted_layers(intent.layers)

    # Build host/standalone primitives (background, stripe) once, keyed by layer id
    # so motif placement can resolve its host via the lanes() contract.
    hosts = {
        layer.id: build_primitive(layer, tile)
        for layer in layers
        if layer.type in ("background", "stripe")
    }

    symbol_defs: dict[str, str] = {}  # motif_id -> <symbol>, deduped, insertion-ordered
    fragments: list[str] = []
    for layer in layers:
        fragment = _render_layer(layer, hosts, palette, colorway_id, tile, symbol_defs)
        if not fragment:
            continue
        if layer.opacity != 1.0:
            fragment = f'<g opacity="{fmt(layer.opacity)}">{fragment}</g>'
        fragments.append(fragment)

    pattern = (
        '<pattern id="tile" patternUnits="userSpaceOnUse" '
        f'width="{fmt(tile)}" height="{fmt(tile)}">'
        f'{"".join(fragments)}</pattern>'
    )
    defs = "".join(symbol_defs.values()) + pattern
    body = f'<rect x="0" y="0" width="{fmt(tile)}" height="{fmt(tile)}" fill="url(#tile)"/>'
    return render_svg_document(body, tile, tile, defs=defs)


def _render_layer(
    layer: Layer,
    hosts: dict[str, object],
    palette: Palette,
    colorway_id: str | None,
    tile: float,
    symbol_defs: dict[str, str],
) -> str:
    if layer.type == "background":
        return hosts[layer.id].render(tile, palette, colorway_id)
    if layer.type == "stripe":
        return hosts[layer.id].render(palette, colorway_id)
    if layer.type == "motif":
        return _render_motif_layer(layer, hosts, palette, colorway_id, tile, symbol_defs)
    raise ValueError(f"unsupported layer type: {layer.type!r}")


def _render_motif_layer(
    layer: MotifLayer,
    hosts: dict[str, object],
    palette: Palette,
    colorway_id: str | None,
    tile: float,
    symbol_defs: dict[str, str],
) -> str:
    placement = layer.placement
    if placement is None:
        raise ValueError(f"motif layer {layer.id!r} requires placement (session 3 scope)")
    host_id = placement.host_layer
    if host_id not in hosts:
        raise ValueError(
            f"motif layer {layer.id!r} references unknown host_layer {host_id!r}"
        )
    if layer.params.color is None:
        raise ValueError(
            f"motif layer {layer.id!r}: multi-color `colors` binding is out of session-3 scope"
        )

    motif = get_motif(layer.params.motif_id)
    symbol_defs.setdefault(motif.id, motif.symbol)

    color = escape_attr(palette.resolve_color(layer.params.color, colorway_id))
    size_mm = layer.params.size_mm
    uses: list[str] = []
    for inst in place(layer, hosts[host_id], tile):
        transform = _instance_transform(motif, inst, size_mm)
        uses.append(
            f'<use href="#motif-{motif.id}" color="{color}" transform="{transform}"/>'
        )
    return "".join(uses)


def _instance_transform(motif: MotifDef, inst: Instance, size_mm: float) -> str:
    """Build the ``<use>`` transform honoring the motif's bbox extent and anchor.

    ``size_mm`` is the desired rendered bounding-box extent, so the scale derives
    from the motif's declared bbox; the anchor is placed exactly on the lane point
    (and is the rotation pivot). Transforms apply right-to-left, so a non-origin
    anchor is shifted to the origin before scale/rotate/translate.
    """
    min_x, min_y, max_x, max_y = motif.bbox_mm
    extent = max(max_x - min_x, max_y - min_y)
    scale = size_mm / extent
    parts = [
        f"translate({fmt(inst.x_mm)} {fmt(inst.y_mm)})",
        f"rotate({fmt(inst.rotation_deg)})",
        f"scale({fmt(scale)})",
    ]
    ax, ay = motif.anchor
    if ax != 0.0 or ay != 0.0:
        parts.append(f"translate({fmt(-ax)} {fmt(-ay)})")
    return " ".join(parts)
