"""Build registered motifs from pinned-font text."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

from app.motifs.registry import MOTIFS, normalize_motif_svg, register_motif

FONT_PATH = Path(__file__).resolve().parent / "assets" / "NotoSansCJKkr-Regular.otf"

# ponytail: words can be very wide; keep the Recraft thin-output gate out of font text.
_TEXT_MAX_ASPECT_RATIO = 1000.0
_FONT_LOCK = threading.Lock()


@dataclass(frozen=True)
class TextMotif:
    motif_id: str
    color: str
    colors: dict[str, str] | None
    warnings: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _font() -> TTFont:
    return TTFont(str(FONT_PATH), lazy=True)


@lru_cache(maxsize=4096)
def _glyph_outline(codepoint: int) -> tuple[str, float] | None:
    with _FONT_LOCK:
        font = _font()
        gname = font.getBestCmap().get(codepoint)
        if gname is None:
            return None
        advance = float(font["hmtx"][gname][0])
        glyphset = font.getGlyphSet()
        pen = SVGPathPen(glyphset)
        glyphset[gname].draw(TransformPen(pen, (1, 0, 0, -1, 0, 0)))
        return pen.getCommands(), advance


def _runs(
    text: str, segments: list[dict] | None, default_color: str
) -> list[tuple[str, float, str]]:
    if not segments:
        return [(text, 1.0, default_color)]
    out: list[tuple[str, float, str]] = []
    for seg in segments:
        s = seg.get("text")
        if not isinstance(s, str) or not s:
            continue
        scale = seg.get("scale")
        scale = (
            float(scale)
            if not isinstance(scale, bool)
            and isinstance(scale, (int, float))
            and scale > 0
            else 1.0
        )
        color = seg.get("color")
        out.append((s, scale, color if isinstance(color, str) and color else default_color))
    return out


def build_text_motif(
    text: str,
    segments: list[dict] | None,
    *,
    default_color: str,
    valid_color_slots: set[str],
) -> TextMotif:
    if not isinstance(text, str):
        raise ValueError("text must be a string")

    warnings: list[str] = []
    warned_colors: set[str] = set()
    color_order: list[str] = []
    color_paints: dict[str, str] = {}
    paths: list[str] = []
    pen_x = 0.0

    for run_text, scale, color in _runs(text, segments, default_color):
        if color not in valid_color_slots:
            if color not in warned_colors:
                warnings.append(
                    f"text color slot {color!r} not in palette; using {default_color!r}"
                )
                warned_colors.add(color)
            color = default_color
        for ch in run_text:
            cp = ord(ch)
            outline = _glyph_outline(cp)
            if outline is None:
                warnings.append(f"no glyph for U+{cp:04X} ({ch!r}); skipped")
                continue
            d, advance = outline
            if d.strip():
                paint = color_paints.get(color)
                if paint is None:
                    color_order.append(color)
                    paint = f"#{len(color_order):06x}"
                    color_paints[color] = paint
                with _FONT_LOCK:
                    font = _font()
                    glyphset = font.getGlyphSet()
                    gname = font.getBestCmap()[cp]
                    pen = SVGPathPen(glyphset)
                    transform = (scale, 0, 0, -scale, pen_x, 0)
                    glyphset[gname].draw(TransformPen(pen, transform))
                paths.append(f'<path d="{pen.getCommands()}" fill="{paint}"/>')
            pen_x += advance * scale

    if not paths:
        raise ValueError("text produced no renderable glyphs")

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000">'
        + "".join(paths)
        + "</svg>"
    )
    motif = normalize_motif_svg(
        svg, max_aspect_ratio=_TEXT_MAX_ASPECT_RATIO, render_check=False
    )
    if motif.id not in MOTIFS:
        register_motif(motif, subject="text", scope="whole", source="text")

    if len(color_order) <= 1:
        return TextMotif(motif.id, color_order[0], None, tuple(warnings))
    if len(motif.color_slots) != len(color_order):
        raise ValueError(
            "text color slot count does not match rendered glyph color count"
        )
    return TextMotif(
        motif.id,
        default_color,
        dict(zip(motif.color_slots, color_order, strict=True)),
        tuple(warnings),
    )
