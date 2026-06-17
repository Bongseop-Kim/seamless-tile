"""Palette and color validation for generated textile SVGs."""

import re

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def is_hex_color(value: str) -> bool:
    return bool(_HEX.match(value))


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


class Colorway:
    def __init__(self, colors):
        colors = tuple(colors)
        if not colors:
            raise ValueError("colorway must have at least one color")
        for color in colors:
            if not is_hex_color(color):
                raise ValueError(f"invalid hex color: {color!r}")
        self.colors = colors

    def __len__(self) -> int:
        return len(self.colors)

    def __getitem__(self, index: int) -> str:
        return self.colors[index % len(self.colors)]
