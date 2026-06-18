"""Primitive generators for background, stripe, dot, and motif layers."""

from app.engine.intent import Layer
from app.engine.primitives.background import Background
from app.engine.primitives.stripe import Stripe, build_stripe

__all__ = ["Background", "Stripe", "build_stripe", "build_primitive"]


def build_primitive(layer: Layer, tile_mm: float):
    """Construct a primitive from a validated layer.

    Stripe snaps its angle to a tile-commensurate direction here. Motif primitives
    are session 3, so unsupported types raise.
    """
    if layer.type == "background":
        return Background(color_slot=layer.params.color)
    if layer.type == "stripe":
        return build_stripe(layer.params, tile_mm)
    raise ValueError(f"unsupported primitive layer type: {layer.type!r}")
