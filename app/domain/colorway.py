"""Colorway: an ordered, validated set of hex colors with cyclic indexing."""

import re

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def is_hex_color(value: str) -> bool:
    return bool(_HEX.match(value))


class Colorway:
    def __init__(self, colors):
        colors = tuple(colors)
        if not colors:
            raise ValueError("colorway must have at least one color")
        for c in colors:
            if not is_hex_color(c):
                raise ValueError(f"invalid hex color: {c!r}")
        self.colors = colors

    def __len__(self) -> int:
        return len(self.colors)

    def __getitem__(self, index: int) -> str:
        return self.colors[index % len(self.colors)]
