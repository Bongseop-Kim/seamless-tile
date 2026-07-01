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
from pathlib import Path

from PIL import Image, ImageChops

from app.core.config import get_settings
from app.engine.composition import compose
from app.engine.palette import Colorway, Palette, rgb_to_hex
from app.render.raster import rasterize
from app.validate.intent import validate_intent

# Tileable weave photos, user-managed (made externally, dropped in). The app only READS
# this directory; it never writes/generates here. Assets are part of the determinism input.
_ASSETS = Path(__file__).parent / "assets" / "fabric"
ASSETS_VERSION = "v2"

# Texture strength: amplifies the weave's darkening in the multiply, pivoted at white so
# highlights stay neutral and the weave shadows deepen -> the texture reads more strongly.
# 1.0 = raw photo (subtle); higher = more pronounced weave.
DEFAULT_TEXTURE_STRENGTH = 2.4


def available_weaves() -> tuple[str, ...]:
    """Valid weave names = the tileable ``<name>.png`` files present in assets/fabric/.

    Discovered from the filesystem so any image the user adds is usable with no code
    change (matches their "images are managed externally" model)."""
    return tuple(sorted(p.stem for p in _ASSETS.glob("*.png")))


def _is_print_weave(weave: str) -> bool:
    # print = the design printed on ONE fabric, woven in a single twill direction.
    return weave.startswith("twill")

_LABEL_COLORWAY_ID = "__fabric_label__"


class FabricError(ValueError):
    """Bad fabric request (unknown weave / colorway / slot). Route maps to 400."""


def _load_texture(weave: str) -> Image.Image:
    path = _ASSETS / f"{weave}.png"
    if not path.exists():
        raise FabricError(
            f"unknown weave: {weave!r}; choose one of {list(available_weaves())}"
        )
    return Image.open(path).convert("RGB")


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
        mapping={sid: rgb_to_hex(*rgb) for sid, rgb in zip(slot_ids, colors)},
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
    return seg, {sid: i for i, sid in enumerate(slot_ids)}


def _mask_for(seg: Image.Image, index: int) -> Image.Image:
    """White where ``seg``'s palette index == ``index``, else black (mode L)."""
    lut: list[int] = []
    for i in range(256):
        v = 255 if i == index else 0
        lut += [v, v, v]
    m = seg.copy()
    m.putpalette(lut)
    return m.convert("L")


def render_fabric(
    intent_raw,
    *,
    colorway_id: str | None = None,
    production_method: str | None = None,
    weave: str = "twill-45",
    material_map: dict[str, str] | None = None,
    dpi: int | None = None,
    texture_strength: float | None = None,
) -> bytes:
    """Approved intent -> textured "cloth" PNG bytes. Deterministic.

    ``production_method`` (``print`` | ``yarn_dyed``) defaults to the intent's
    ``production.method`` (prompt-interpreted or user-set); pass it to override.
    - **print**: the design is printed onto one fabric -> a single twill weave
      (``twill-0``/``twill-45``) covers the whole tile; ``material_map`` is rejected.
    - **yarn_dyed**: woven from pre-dyed yarns -> ``material_map`` mixes weaves per color
      slot (``solid``/``twill-0``/``twill-45``/``herringbone``); unmapped slots fall back
      to ``weave``.

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
    elif material_map:  # yarn_dyed per-region
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
    tile_mm = intent.canvas.tile_mm

    design_svg = compose(intent, palette, colorway_id)
    design_png, _ = rasterize(design_svg, "png", dpi=dpi, width_mm=tile_mm)
    design = Image.open(io.BytesIO(design_png)).convert("RGB")

    out = _apply_weave(design, weave, strength)  # uniform base / fallback for unmapped slots
    if material_map:
        seg, index_for = _segment(intent, palette, dpi=dpi, tile_mm=tile_mm)
        # Regions are disjoint (each pixel has one nearest label), so composite order is
        # irrelevant -> result is independent of material_map dict order (deterministic).
        for slot, slot_weave in material_map.items():
            mask = _mask_for(seg, index_for[slot])
            out = Image.composite(_apply_weave(design, slot_weave, strength), out, mask)

    buf = io.BytesIO()
    out.save(buf, "PNG", dpi=(dpi, dpi))
    return buf.getvalue()
