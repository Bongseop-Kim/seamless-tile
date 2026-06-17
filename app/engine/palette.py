"""Palette, color slots, and colorways for generated textile SVGs.

Color resolution rule (see ARCHITECTURE.md): a slot's ``hex`` is a *preview* and is
not authoritative. Output color is always resolved through the active colorway's
mapping. A ``default`` colorway is mandatory and is used when no colorway is given.
"""

import colorsys
import re
from dataclasses import dataclass

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def is_hex_color(value: str) -> bool:
    return bool(_HEX.match(value))


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def out_of_gamut(hex_color: str) -> bool:
    """Conservative heuristic flagging near-pure, highly saturated sRGB colors that
    typically fall outside the CMYK/spot gamut. Not a substitute for ICC profiling;
    used only to emit non-blocking warnings during validation."""
    if not is_hex_color(hex_color):
        return False
    r, g, b = _hex_to_rgb(hex_color)
    _, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return s > 0.95 and v > 0.9


# --- Named preset palettes (served by GET /api/v1/palettes) -------------------

PALETTES: dict[str, tuple[str, ...]] = {
    "mono": ("#ffffff", "#1a1a1a"),
    "navy": ("#f4f4f0", "#1f3a5f"),
    "earth": ("#e8ddcb", "#8b5e3c", "#3e2f1c"),
    "pastel": ("#fce4ec", "#b3e5fc", "#c8e6c9"),
}


def resolve_palette(name: str) -> tuple[str, ...]:
    try:
        return PALETTES[name]
    except KeyError:
        raise ValueError(
            f"unknown palette: {name!r}; choose one of {sorted(PALETTES)}"
        ) from None


# --- Color slots & colorways --------------------------------------------------

DEFAULT_COLORWAY_ID = "default"


@dataclass(frozen=True)
class ColorSlot:
    """A named color position. ``hex`` is preview-only; the colorway is canonical."""

    id: str
    hex: str
    spot: str | None = None  # Pantone/TCX production color
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("color slot id must be non-empty")
        if not is_hex_color(self.hex):
            raise ValueError(f"invalid hex color: {self.hex!r}")


@dataclass(frozen=True)
class Colorway:
    """Maps each slot id to a concrete output color (hex or spot)."""

    id: str
    mapping: dict[str, str]
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("colorway id must be non-empty")
        if not self.mapping:
            raise ValueError("colorway mapping must not be empty")

    def color_for(self, slot_id: str) -> str:
        try:
            return self.mapping[slot_id]
        except KeyError:
            raise ValueError(
                f"colorway {self.id!r} has no mapping for slot {slot_id!r}"
            ) from None


@dataclass(frozen=True)
class Palette:
    """Color slots plus the colorways that resolve them. A ``default`` colorway is
    required, and every colorway must map exactly the declared slots."""

    slots: tuple[ColorSlot, ...]
    colorways: tuple[Colorway, ...]

    def __post_init__(self) -> None:
        if not self.slots:
            raise ValueError("palette must have at least one color slot")
        slot_ids = [s.id for s in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("duplicate color slot id")

        cw_ids = [c.id for c in self.colorways]
        if len(cw_ids) != len(set(cw_ids)):
            raise ValueError("duplicate colorway id")
        if DEFAULT_COLORWAY_ID not in cw_ids:
            raise ValueError(f"a {DEFAULT_COLORWAY_ID!r} colorway is required")

        known = set(slot_ids)
        for cw in self.colorways:
            unknown = set(cw.mapping) - known
            if unknown:
                raise ValueError(
                    f"colorway {cw.id!r} maps unknown slots: {sorted(unknown)}"
                )
            missing = known - set(cw.mapping)
            if missing:
                raise ValueError(
                    f"colorway {cw.id!r} missing slots: {sorted(missing)}"
                )

    def slot_ids(self) -> set[str]:
        return {s.id for s in self.slots}

    def colorway(self, colorway_id: str | None) -> Colorway:
        target = colorway_id or DEFAULT_COLORWAY_ID
        for cw in self.colorways:
            if cw.id == target:
                return cw
        raise ValueError(f"unknown colorway: {colorway_id!r}")

    def resolve_color(self, slot_id: str, colorway_id: str | None = None) -> str:
        if slot_id not in self.slot_ids():
            raise ValueError(f"unknown color slot: {slot_id!r}")
        return self.colorway(colorway_id).color_for(slot_id)

    def distinct_colors(self, colorway_id: str | None = None) -> set[str]:
        cw = self.colorway(colorway_id)
        return {cw.color_for(s.id) for s in self.slots}
