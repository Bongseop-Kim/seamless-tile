"""Deterministic fabric texturing (session 15, ``/finalize``).

Composite a bundled *tileable* weave photo onto a rasterized seamless design to make a
"cloth" PNG. No generative model, no external calls — local Pillow pixel ops only, so
identical inputs give byte-identical PNGs (pinned renderer/Pillow/assets). Because both
the design tile and the weave texture tile, the output stays seamless.

The design is re-composed from the intent (``compose`` is deterministic -> byte-identical
to the approved preview) rather than looked up. The intent is needed anyway for per-region
texturing: a synthetic "label colorway" paints each color slot a distinct flat color, and
that render is quantized into per-slot masks (§5.6). The engine stays material-agnostic —
the weave/material map lives only here, never in the intent.

Security: only clean (filter-free) engine SVG is rasterized; the sanitizer allowlist is
untouched. Textures are applied purely in the raster stage.
"""

from __future__ import annotations

import colorsys
import io
import math
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

from app.core.config import get_settings
from app.engine.composition import compose
from app.engine.palette import Colorway, Palette, rgb_to_hex
from app.render.raster import rasterize
from app.validate.intent import validate_intent

# Tileable weave photos, user-managed (made externally, dropped in). The app only READS
# this directory; it never writes/generates here. Assets are part of the determinism input.
_ASSETS = Path(__file__).parent / "assets" / "fabric"

# Texture strength: amplifies the weave's darkening in the multiply, pivoted at white so
# highlights stay neutral and the weave shadows deepen -> the texture reads more strongly.
# 1.0 = raw photo (subtle); higher = more pronounced weave.
DEFAULT_TEXTURE_STRENGTH = 2.4

# Relief (yarn-dyed only): embosses color-slot boundaries so woven regions read as raised
# threads. 1.0 = default bevel; 0 = off. Rim width is physical (mm), so it's DPI-stable.
# Also scales the motif strand-edge shading (_apply_thread_relief).
DEFAULT_RELIEF_STRENGTH = 0.45
_RELIEF_MM = 0.17  # ~raised-yarn width; boundary rim ≈ this wide regardless of dpi
# Rim intensity floor as a fraction of full: the weave luminance modulates the rim between
# this and 1.0, so the raised line breaks up unevenly (real weaving isn't uniform) instead
# of a clean outline. Lower = bumpier/patchier.
_RELIEF_RIM_MIN = 0.25


def available_weaves() -> tuple[str, ...]:
    """Valid weave names = the tileable ``<name>.png`` files present in assets/fabric/.

    Discovered from the filesystem so any image the user adds is usable with no code
    change (matches their "images are managed externally" model)."""
    return tuple(sorted(p.stem for p in _ASSETS.glob("*.png")))


def _is_print_weave(weave: str) -> bool:
    # print = the design printed on ONE fabric, woven in a single twill direction.
    return weave.startswith("twill")

# yarn-dyed global rule: motif yarn surface is ALWAYS design x twill-45 — fixed,
# ignoring the base weave and material_map (flat strands read as plastic; per-slot
# weaves read as patchwork). Only if the asset is missing do strands fall back to the
# flat design color.
MOTIF_WEAVE = "twill-45"
MOTIF_THREAD_PERIOD_MM = 0.70
MOTIF_THREAD_FILL = 0.82
_MOTIF_THREAD_AA_SCALE = 3
_MOTIF_MASK_THRESHOLD = 24  # motif coverage below this alpha/level is not yarn
_THREAD_RELIEF_MM = 0.04  # strand-edge shading offset (physical, DPI-stable)
_THREAD_SHADE_K = 0.23  # shading per unit relief_strength; 0.23 * default 0.7 ≈ 0.16


def _motif_slots(intent) -> set[str]:
    """Palette slot ids a motif's fills resolve to, across all motif layers.

    MotifParams sets exactly one of ``color`` (single-color -> one slot) or ``colors``
    (fill_slot -> palette_slot map). Non-motif layers are skipped, so their params are
    never touched."""
    slots: set[str] = set()
    for layer in intent.layers:
        if layer.type != "motif":
            continue
        if layer.params.color is not None:
            slots.add(layer.params.color)
        if layer.params.colors:
            slots.update(layer.params.colors.values())
    return slots

_LABEL_COLORWAY_ID = "__fabric_label__"


class FabricError(ValueError):
    """Bad fabric request (unknown weave / colorway / slot). Route maps to 400."""


@lru_cache(maxsize=32)
def _load_texture_file(path: str) -> Image.Image:
    return Image.open(path).convert("RGB")


def _load_texture(weave: str) -> Image.Image:
    path = _ASSETS / f"{weave}.png"
    if not path.exists():
        raise FabricError(
            f"unknown weave: {weave!r}; choose one of {list(available_weaves())}"
        )
    return _load_texture_file(str(path))


def _tile_to(texture: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Wrap-tile a tileable ``texture`` to exactly ``size``, preserving seamlessness.

    Paste an integer number of copies into a seamless canvas, then resize that canvas to
    the target. Cropping is deliberately avoided: a partial trailing tile breaks the seam
    when the target width isn't a multiple of the texture width.
    """
    w, h = size
    tw, th = texture.size
    nx = max(1, round(w / tw))
    ny = max(1, round(h / th))
    canvas = Image.new("RGB", (nx * tw, ny * th))
    for j in range(ny):
        for i in range(nx):
            canvas.paste(texture, (i * tw, j * th))
    if canvas.size != size:
        canvas = canvas.resize(size, Image.Resampling.LANCZOS)
    return canvas


def _apply_weave(design_rgb: Image.Image, weave: str, strength: float) -> Image.Image:
    """Multiply the tiled weave over the design, amplifying the weave's darkening by
    ``strength`` (pivot at white) so the texture reads more strongly. ``strength`` 1.0 =
    raw photo."""
    tex = _tile_to(_load_texture(weave), design_rgb.size)
    if strength != 1.0:
        tex = tex.point(lambda v: max(0, min(255, round(255 - (255 - v) * strength))))
    return ImageChops.multiply(design_rgb, tex)


def _render_design(intent, palette, colorway_id: str | None, *, dpi: int, tile_mm: float):
    svg = compose(intent, palette, colorway_id)
    png, _ = rasterize(svg, "png", dpi=dpi, width_mm=tile_mm)
    return Image.open(io.BytesIO(png)).convert("RGB")


def _apply_materials(
    design: Image.Image,
    *,
    weave: str,
    material_map: dict[str, str] | None,
    strength: float,
    seg: Image.Image | None = None,
    index_for: dict[str, int] | None = None,
) -> Image.Image:
    woven_cache: dict[tuple[str, float], Image.Image] = {}

    def woven(slot_weave: str) -> Image.Image:
        key = (slot_weave, strength)
        cached = woven_cache.get(key)
        if cached is None:
            cached = _apply_weave(design, slot_weave, strength)
            woven_cache[key] = cached
        return cached

    out = woven(weave)
    if not material_map:
        return out
    assert seg is not None and index_for is not None, "material_map requires segmentation"
    # Regions are disjoint (each pixel has one nearest label), so composite order is
    # irrelevant -> result is independent of material_map dict order (deterministic).
    for slot, slot_weave in material_map.items():
        mask = _mask_for(seg, index_for[slot])
        out = Image.composite(woven(slot_weave), out, mask)
    return out


def _label_colors(n: int) -> list[tuple[int, int, int]]:
    """``n`` maximally-spread, distinct RGB colors, deterministic from the count.

    ponytail: evenly spaced full-sat hues. Distinct for the small slot counts palettes use
    (PaletteSpec caps at 64); very large n could crowd hues — bump to an RGB cube then.
    """
    return [
        tuple(round(c * 255) for c in colorsys.hsv_to_rgb(i / max(1, n), 1.0, 1.0))
        for i in range(n)
    ]


def _segment(intent, palette, *, dpi: int, tile_mm: float):
    """Render a per-slot segmentation via a synthetic label colorway, then quantize it to
    clean masks. Returns ``(seg_P_image, {slot_id: palette_index}, alpha_L_image)``.

    Alpha matters only for partial renders (e.g. motif-only intents with transparent host
    layers); full-canvas intents yield an all-opaque channel callers can ignore."""
    slot_ids = sorted(palette.slot_ids())  # deterministic order
    colors = _label_colors(len(slot_ids))
    label_cw = Colorway(
        id=_LABEL_COLORWAY_ID,
        mapping={sid: rgb_to_hex(*rgb) for sid, rgb in zip(slot_ids, colors, strict=True)},
    )
    label_palette = Palette(slots=palette.slots, colorways=palette.colorways + (label_cw,))
    label_svg = compose(intent, label_palette, _LABEL_COLORWAY_ID)
    png, _ = rasterize(label_svg, "png", dpi=dpi, width_mm=tile_mm)
    rgba = Image.open(io.BytesIO(png)).convert("RGBA")

    # Quantize to exactly the label colors (nearest-label, no dither) so anti-aliased
    # boundaries discretize to one region.
    pal_img = Image.new("P", (1, 1))
    flat = [c for rgb in colors for c in rgb]
    flat += [0, 0, 0] * (256 - len(colors))
    pal_img.putpalette(flat)
    seg = rgba.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.NONE)
    index_for = dict(zip(slot_ids, range(len(slot_ids)), strict=True))
    return seg, index_for, rgba.getchannel("A")


def _mask_for(seg: Image.Image, index: int) -> Image.Image:
    """White where ``seg``'s palette index == ``index``, else black (mode L)."""
    lut: list[int] = []
    for i in range(256):
        v = 255 if i == index else 0
        lut += [v, v, v]
    m = seg.copy()
    m.putpalette(lut)
    return m.convert("L")


def _without_motif_layers(intent):
    layers = [layer for layer in intent.layers if layer.type != "motif"]
    if not layers:
        return None
    return intent.model_copy(update={"layers": layers})


def _motif_only_layers(intent):
    """Render only motifs, while keeping transparent host layers for path placement."""
    layers = []
    has_motif = False
    for layer in intent.layers:
        if layer.type == "motif":
            has_motif = True
            layers.append(layer)
        else:
            layers.append(layer.model_copy(update={"opacity": 0.0}))
    if not has_motif:
        return None
    return intent.model_copy(update={"layers": layers})


def _thread_period_width(size: tuple[int, int], *, dpi: int) -> tuple[Fraction, int]:
    """Strand spacing (exact rational, px) and strand width (px).

    The spacing is ``gcd(w, h) / n`` — an exact integer division of both axes — so the
    diagonal line family repeats with the tile in both directions and a motif crossing
    the tile edge keeps its strand phase. A rational step (instead of hunting an integer
    divisor) stays near the mm target even when the pixel size is prime; an
    integer-divisor hunt collapses to one tile-wide strand for e.g. 787px/1181px tiles.
    Exactness matters: line positions are floored rationals, so the set is invariant
    under shifts by w/h with no float rounding at the seam.
    """
    target = max(2.0, MOTIF_THREAD_PERIOD_MM * dpi / 25.4)
    g = math.gcd(*size)
    step = Fraction(g, max(1, round(g / target)))
    width = max(1, min(math.ceil(step) - 1, round(step * MOTIF_THREAD_FILL)))
    return step, width


def _tile_3x(mask: Image.Image) -> Image.Image:
    w, h = mask.size
    tiled = Image.new("L", (w * 3, h * 3))
    for ty in range(3):
        for tx in range(3):
            tiled.paste(mask, (tx * w, ty * h))
    return tiled


def _draw_round_thread(
    draw: ImageDraw.ImageDraw,
    p0: tuple[int, int],
    p1: tuple[int, int],
    *,
    width: int,
    scale: int,
) -> None:
    x0, y0 = p0
    x1, y1 = p1
    q0 = (x0 * scale + scale // 2, y0 * scale + scale // 2)
    q1 = (x1 * scale + scale // 2, y1 * scale + scale // 2)
    stroke = max(1, width * scale)
    radius = stroke / 2
    draw.line((q0, q1), fill=255, width=stroke)
    for x, y in (q0, q1):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)


def _motif_thread_mask(motif_mask: Image.Image, *, dpi: int) -> Image.Image:
    """Draw motif yarns as stacked ``/`` capsules instead of clipping straight bands.

    The motif mask is tiled 3x3 before scanning, so a motif instance that crosses a tile
    edge keeps a continuous strand phase across the seam. Each diagonal run is shortened
    before drawing, which gives the visible rounded end caps users expect from woven
    yarns and naturally drops tiny details that yarn-dyed production cannot preserve.
    """
    w, h = motif_mask.size
    step, width = _thread_period_width(motif_mask.size, dpi=dpi)
    tiled = _tile_3x(motif_mask).point(lambda v: 255 if v >= _MOTIF_MASK_THRESHOLD else 0)
    bw, bh = tiled.size
    scale = _MOTIF_THREAD_AA_SCALE
    drawn = Image.new("L", (bw * scale, bh * scale), 0)
    draw = ImageDraw.Draw(drawn)
    px = tiled.load()
    # Native diagonal samples are sqrt(2) px apart. Inset by roughly a radius so caps sit
    # inside the motif instead of being chopped flat by the original SVG boundary.
    inset = max(1, math.ceil((width / 2) / math.sqrt(2)))
    min_run = inset * 2 + 1
    center_phase = width // 2

    def emit_run(coords: list[tuple[int, int]], start: int, end: int) -> None:
        if end - start + 1 < min_run:
            return
        _draw_round_thread(
            draw,
            coords[start + inset],
            coords[end - inset],
            width=width,
            scale=scale,
        )

    # int(k * step) is an exact rational floor, so the line set is invariant under
    # shifts by bw/bh (both are multiples of step) — the center crop tiles seamlessly.
    for k in range(math.ceil((bw + bh - 1 - center_phase) / step)):
        c = center_phase + int(k * step)
        x0 = max(0, c - (bh - 1))
        x1 = min(bw - 1, c)
        if x1 < x0:
            continue
        coords = [(x, c - x) for x in range(x0, x1 + 1)]
        run_start: int | None = None
        for i, (x, y) in enumerate(coords):
            if px[x, y]:
                if run_start is None:
                    run_start = i
            elif run_start is not None:
                emit_run(coords, run_start, i - 1)
                run_start = None
        if run_start is not None:
            emit_run(coords, run_start, len(coords) - 1)

    strands = drawn.resize((bw, bh), Image.Resampling.LANCZOS)
    return strands.crop((w, h, w * 2, h * 2))


def _apply_thread_relief(
    out: Image.Image, mask: Image.Image, strength: float, *, dpi: int
) -> Image.Image:
    """Shade strand edges (light top-left, shadow bottom-right) so capsules read round.

    Follows the same ``relief_strength`` knob as the slot-boundary emboss: ``0`` means a
    fully flat render, matching the documented "0 disables" contract."""
    d = max(1, round(_THREAD_RELIEF_MM * dpi / 25.4))
    hi = ImageChops.subtract(mask, ImageChops.offset(mask, d, d))
    lo = ImageChops.subtract(mask, ImageChops.offset(mask, -d, -d))
    k = min(0.5, _THREAD_SHADE_K * strength)
    lit = Image.blend(out, Image.new("RGB", out.size, (255, 255, 255)), k)
    dark = Image.blend(out, Image.new("RGB", out.size, (0, 0, 0)), k)
    out = Image.composite(lit, out, hi)
    return Image.composite(dark, out, lo)


def _motif_slot_masks(
    intent,
    palette,
    *,
    dpi: int,
    tile_mm: float,
) -> dict[str, Image.Image]:
    """Per-slot masks for motif pixels only, excluding stripe/background reuse of slots."""
    motif_intent = _motif_only_layers(intent)
    motif_slots = sorted(_motif_slots(intent))
    if motif_intent is None or not motif_slots:
        return {}
    # Alpha gates out the transparent host layers, leaving motif pixels only.
    seg, index_for, alpha = _segment(motif_intent, palette, dpi=dpi, tile_mm=tile_mm)
    return {
        slot: ImageChops.multiply(_mask_for(seg, index_for[slot]), alpha)
        for slot in motif_slots
    }


def _apply_motif_thread_inlay(
    out: Image.Image,
    design: Image.Image,
    intent,
    palette,
    colorway_id: str | None,
    *,
    weave: str,
    material_map: dict[str, str] | None,
    strength: float,
    relief: float,
    dpi: int,
    tile_mm: float,
) -> Image.Image:
    """Replace yarn-dyed motif fills with diagonal yarn strands over the base fabric.

    Strand pixels are ``design`` x the fixed ``MOTIF_WEAVE`` twill — the base ``weave``
    and ``material_map`` never touch them, so every motif yarn reads as the same thread
    stock regardless of how the ground is woven."""
    base_intent = _without_motif_layers(intent)
    if base_intent is None:
        return out

    masks = _motif_slot_masks(intent, palette, dpi=dpi, tile_mm=tile_mm)
    if not masks:
        return out

    base_design = _render_design(
        base_intent, palette, colorway_id, dpi=dpi, tile_mm=tile_mm
    )
    base_seg = base_index_for = None
    if material_map:
        base_seg, base_index_for, _ = _segment(
            base_intent, palette, dpi=dpi, tile_mm=tile_mm
        )
    base = _apply_materials(
        base_design,
        weave=weave,
        material_map=material_map,
        strength=strength,
        seg=base_seg,
        index_for=base_index_for,
    )
    # Runs are scanned per slot (a capsule must end at a color boundary), but all strands
    # pick their pixels from the same flat `design`, so one union mask composites them in
    # a single pass — no slot-order dependence, and edge shading is applied exactly once.
    thread: Image.Image | None = None
    for motif_mask in masks.values():
        slot_thread = _motif_thread_mask(motif_mask, dpi=dpi)
        thread = slot_thread if thread is None else ImageChops.lighter(thread, slot_thread)
    yarn = (
        _apply_weave(design, MOTIF_WEAVE, strength)
        if MOTIF_WEAVE in available_weaves()
        else design  # asset missing -> flat strands beat failing the render
    )
    base = Image.composite(yarn, base, thread)
    if relief > 0:
        base = _apply_thread_relief(base, thread, relief, dpi=dpi)
    return base


def _apply_relief(
    out: Image.Image, seg: Image.Image, weave: str, strength: float, *, dpi: int
) -> Image.Image:
    """Emboss color-slot boundaries so yarn-dyed regions read as raised woven threads.

    Deterministic and seamless: the rim comes from wrap-around ``ImageChops.offset`` (a
    circular shift), so it stays continuous across the tile seam — no blur (blur would not
    wrap and would open the seam). Light is fixed top-left: each boundary's up-left side is
    highlighted, its down-right side shadowed. Rim width tracks ``dpi`` (``_RELIEF_MM``) so
    the raised look is DPI-stable.

    The rim is *modulated by the (tileable, deterministic) weave luminance* so the raised
    line varies along its length — real weaving isn't uniform, so a flat outline reads as
    fake. The weave photo is already seamless, so the modulation is too.

    ponytail: reuse the weave asset as the unevenness field instead of a bespoke seeded
    noise generator — same look, no new code, and physically it's the actual thread grain.
    """
    d = max(1, round(_RELIEF_MM * dpi / 25.4))  # rim width in px, ~constant physical size
    idx = Image.frombytes("L", seg.size, seg.tobytes())  # slot index per pixel (0..n-1)

    def rim(dx: int, dy: int) -> Image.Image:
        # nonzero where the (dx,dy) neighbor is a different slot; offset wraps -> seam-safe
        return ImageChops.difference(idx, ImageChops.offset(idx, dx, dy)).point(
            lambda v: 255 if v else 0
        )

    # Uneven thread height: full-contrast weave luminance mapped to [_RELIEF_RIM_MIN, 1].
    # autocontrast normalizes any weave photo so the bumpiness is visible regardless of its
    # native contrast. Per-pixel LUT + a tiled seamless source => still seamless.
    tex = ImageOps.autocontrast(_tile_to(_load_texture(weave), out.size).convert("L"), cutoff=1)
    mod = tex.point(lambda v: round(255 * (_RELIEF_RIM_MIN + (1 - _RELIEF_RIM_MIN) * v / 255)))

    hi = ImageChops.multiply(rim(d, d), mod)     # up-left face, lit, roughened
    lo = ImageChops.multiply(rim(-d, -d), mod)   # down-right face, shadowed, roughened
    k = min(0.6, 0.26 * strength)  # gentler than a hard bevel; mod drops it further locally
    lit = Image.blend(out, Image.new("RGB", out.size, (255, 255, 255)), k)
    dark = Image.blend(out, Image.new("RGB", out.size, (0, 0, 0)), k)
    out = Image.composite(lit, out, hi)
    return Image.composite(dark, out, lo)


def render_fabric(
    intent_raw,
    *,
    colorway_id: str | None = None,
    production_method: str | None = None,
    weave: str = "twill-45",
    material_map: dict[str, str] | None = None,
    dpi: int | None = None,
    texture_strength: float | None = None,
    relief_strength: float | None = None,
) -> bytes:
    """Approved intent -> textured "cloth" PNG bytes. Deterministic.

    ``production_method`` (``print`` | ``yarn_dyed``) defaults to the intent's
    ``production.method`` (prompt-interpreted or user-set); pass it to override.
    - **print**: the design is printed onto one fabric -> a single twill weave
      (``twill-0``/``twill-45``) covers the whole tile; ``material_map`` is rejected.
    - **yarn_dyed**: woven from pre-dyed yarns -> ``material_map`` mixes weaves per color
      slot (``solid``/``twill-0``/``twill-45``/``herringbone``); unmapped slots fall back
      to ``weave``. Motif regions are redrawn as seamless ``/`` diagonal round-cap
      strands so the base fabric shows between yarns; the strand surface is fixed to
      design x ``MOTIF_WEAVE`` (twill-45) — the base ``weave`` and ``material_map``
      never affect it (a map entry for a motif slot only touches base-fabric pixels).
      Color-slot boundaries are embossed and strand edges shaded (both follow
      ``relief_strength``) so motifs read as raised threads; ``0`` disables all relief.
      Relief/thread-inlay are ignored for print (flat ink).

    Raises ``IntentInvalid`` (bad intent -> 422), ``FabricError`` (bad knob -> 400), or
    ``RasterError`` (no/failed renderer -> 502).
    """
    settings = get_settings()
    result = validate_intent(intent_raw)
    intent = result.intent
    palette = result.palette
    method = production_method or intent.production.method
    weaves = available_weaves()

    if method not in ("print", "yarn_dyed"):
        raise FabricError(f"unknown production_method: {method!r}")
    if weave not in weaves:
        raise FabricError(f"unknown weave: {weave!r}; choose one of {list(weaves)}")
    if colorway_id is not None and colorway_id not in {c.id for c in palette.colorways}:
        raise FabricError(f"unknown colorway: {colorway_id!r}")

    if method == "print":
        if not _is_print_weave(weave):
            raise FabricError(f"print uses a twill weave (twill-*); got {weave!r}")
        if material_map:
            raise FabricError(
                "print applies a uniform weave; material_map is only for yarn_dyed"
            )
        material_map = None
    else:  # yarn_dyed
        if material_map:  # per-region overrides
            unknown_slots = sorted(set(material_map) - palette.slot_ids())
            if unknown_slots:
                raise FabricError(f"material_map references unknown slots: {unknown_slots}")
            bad_weaves = sorted(set(material_map.values()) - set(weaves))
            if bad_weaves:
                raise FabricError(f"material_map uses unknown weaves: {bad_weaves}")
    dpi = dpi or settings.fabric_dpi
    if dpi > settings.max_dpi:
        raise FabricError(f"dpi {dpi} exceeds max_dpi {settings.max_dpi}")
    strength = DEFAULT_TEXTURE_STRENGTH if texture_strength is None else texture_strength
    if strength < 0:
        raise FabricError(f"texture_strength must be >= 0; got {strength}")
    relief = DEFAULT_RELIEF_STRENGTH if relief_strength is None else relief_strength
    if relief < 0:
        raise FabricError(f"relief_strength must be >= 0; got {relief}")
    apply_relief = method == "yarn_dyed" and relief > 0  # raised threads: yarn-dyed only
    tile_mm = intent.canvas.tile_mm

    design = _render_design(intent, palette, colorway_id, dpi=dpi, tile_mm=tile_mm)

    seg = index_for = None
    if material_map or apply_relief:
        seg, index_for, _ = _segment(intent, palette, dpi=dpi, tile_mm=tile_mm)
    out = _apply_materials(
        design,
        weave=weave,
        material_map=material_map,
        strength=strength,
        seg=seg,
        index_for=index_for,
    )
    if method == "yarn_dyed":
        out = _apply_motif_thread_inlay(
            out,
            design,
            intent,
            palette,
            colorway_id,
            weave=weave,
            material_map=material_map,
            strength=strength,
            relief=relief,
            dpi=dpi,
            tile_mm=tile_mm,
        )
    if apply_relief:  # emboss slot boundaries on top of the woven surface (seg exists)
        out = _apply_relief(out, seg, weave, relief, dpi=dpi)

    buf = io.BytesIO()
    out.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()
