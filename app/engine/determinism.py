"""Determinism helpers: stable ordering, seeded RNG, reproduction metadata.

The engine guarantees that the same intent_version + intent + seed + colorway
produces a byte-identical SVG. These helpers fix the moving parts: layer order,
randomness, and the metadata recorded with each candidate.
"""

import random
from dataclasses import dataclass

from app.core.config import ENGINE_VERSION, REGISTRY_VERSION


def layer_sort_key(layer) -> tuple[int, str]:
    return (layer.z_order, layer.id)


def sorted_layers(layers):
    """Stable order: z_order, then id. Pure; does not mutate the input."""
    return sorted(layers, key=layer_sort_key)


def seeded_rng(seed: int) -> random.Random:
    """A standalone RNG seeded only by `seed` (never the global random module)."""
    return random.Random(seed)


@dataclass(frozen=True)
class ReproMeta:
    intent_version: int
    engine_version: str
    registry_version: str
    seed: int
    colorway_id: str
    layout_id: str | None = None

    @classmethod
    def build(
        cls,
        *,
        intent_version: int,
        seed: int,
        colorway_id: str,
        layout_id: str | None = None,
    ) -> "ReproMeta":
        return cls(
            intent_version=intent_version,
            engine_version=ENGINE_VERSION,
            registry_version=REGISTRY_VERSION,
            seed=seed,
            colorway_id=colorway_id,
            layout_id=layout_id,
        )
