"""Texture registry: map a request string to a Texture instance."""

from app.texture.base import Texture
from app.texture.linen import LinenTexture
from app.texture.noise import NoiseTexture
from app.texture.weave import WeaveTexture

_REGISTRY = {
    "weave": WeaveTexture,
    "linen": LinenTexture,
    "noise": NoiseTexture,
}

KNOWN_TEXTURES = frozenset(_REGISTRY)


def texture_from_name(name: str | None) -> Texture | None:
    if name is None:
        return None
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(f"unknown texture: {name!r}") from None
