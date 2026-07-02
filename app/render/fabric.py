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
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

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
DEFAULT_RELIEF_STRENGTH = 0.7
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

# yarn-dyed global rule: motifs are woven in this one twill (mirrors print's uniform
# twill). Motif color slots override the per-region material_map; stripe/background slots
# keep their varied weaves. Skipped only if the asset is absent.
MOTIF_WEAVE = "twill-45"


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
    clean masks. Returns ``(seg_P_image, {slot_id: palette_index})``."""
    slot_ids = sorted(palette.slot_ids())  # deterministic order
    colors = _label_colors(len(slot_ids))
    label_cw = Colorway(
        id=_LABEL_COLORWAY_ID,
        mapping={sid: rgb_to_hex(*rgb) for sid, rgb in zip(slot_ids, colors, strict=True)},
    )
    label_palette = Palette(slots=palette.slots, colorways=palette.colorways + (label_cw,))
    label_svg = compose(intent, label_palette, _LABEL_COLORWAY_ID)
    png, _ = rasterize(label_svg, "png", dpi=dpi, width_mm=tile_mm)
    seg_rgb = Image.open(io.BytesIO(png)).convert("RGB")

    # Quantize to exactly the label colors (nearest-label, no dither) so anti-aliased
    # boundaries discretize to one region.
    pal_img = Image.new("P", (1, 1))
    flat = [c for rgb in colors for c in rgb]
    flat += [0, 0, 0] * (256 - len(colors))
    pal_img.putpalette(flat)
    seg = seg_rgb.quantize(palette=pal_img, dither=Image.Dither.NONE)
    return seg, dict(zip(slot_ids, range(len(slot_ids)), strict=True))


def _mask_for(seg: Image.Image, index: int) -> Image.Image:
    """White where ``seg``'s palette index == ``index``, else black (mode L)."""
    lut: list[int] = []
    for i in range(256):
        v = 255 if i == index else 0
        lut += [v, v, v]
    m = seg.copy()
    m.putpalette(lut)
    return m.convert("L")


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
      to ``weave``. Unmapped motif color slots default to ``MOTIF_WEAVE`` (twill-45) so a
      motif reads as one uniform fabric, but an explicit ``material_map`` entry for a
      motif slot wins. Color-slot boundaries are also
      embossed (``relief_strength``) so motifs read as raised threads; ``0`` disables.
      Relief/pin are ignored for print (flat ink).

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
        # Motifs default to one uniform twill so an unmapped motif reads as one fabric —
        # but an explicit per-region weave (user's material_map) wins over the pin. Slots
        # come from the validated intent (already valid palette ids). Skipped if the twill
        # asset is missing.
        if MOTIF_WEAVE in weaves:
            motif_pins = {s: MOTIF_WEAVE for s in _motif_slots(intent)}
            if motif_pins:
                material_map = {**motif_pins, **(material_map or {})}

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

    design_svg = compose(intent, palette, colorway_id)
    design_png, _ = rasterize(design_svg, "png", dpi=dpi, width_mm=tile_mm)
    design = Image.open(io.BytesIO(design_png)).convert("RGB")

    woven_cache: dict[tuple[str, float], Image.Image] = {}

    def woven(slot_weave: str) -> Image.Image:
        key = (slot_weave, strength)
        cached = woven_cache.get(key)
        if cached is None:
            cached = _apply_weave(design, slot_weave, strength)
            woven_cache[key] = cached
        return cached

    out = woven(weave)  # uniform base / fallback for unmapped slots
    if material_map or apply_relief:
        seg, index_for = _segment(intent, palette, dpi=dpi, tile_mm=tile_mm)
        # Regions are disjoint (each pixel has one nearest label), so composite order is
        # irrelevant -> result is independent of material_map dict order (deterministic).
        for slot, slot_weave in (material_map or {}).items():
            mask = _mask_for(seg, index_for[slot])
            out = Image.composite(woven(slot_weave), out, mask)
        if apply_relief:  # emboss slot boundaries on top of the woven surface
            out = _apply_relief(out, seg, weave, relief, dpi=dpi)

    buf = io.BytesIO()
    out.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()
