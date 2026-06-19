"""Determinism helpers: stable ordering, seeded RNG, reproduction metadata.

The engine guarantees that the same intent_version + intent + seed + colorway
produces a byte-identical SVG. These helpers fix the moving parts: layer order,
randomness, and the metadata recorded with each candidate.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.config import ENGINE_VERSION, REGISTRY_VERSION

if TYPE_CHECKING:
    from app.engine.intent import Intent


def layer_sort_key(layer) -> tuple[int, str]:
    return (layer.z_order, layer.id)


def sorted_layers(layers):
    """Stable order: z_order, then id. Pure; does not mutate the input."""
    return sorted(layers, key=layer_sort_key)


def seeded_rng(seed: int) -> random.Random:
    """A standalone RNG seeded only by `seed` (never the global random module)."""
    return random.Random(seed)


def layout_id_for(intent: "Intent") -> str:
    """Stable id of the placement+symmetry configuration that defines a layout.

    Hashes the intent's structural fields and deliberately excludes ``seed``,
    ``colorways`` and ``palette`` so that two candidates differing only by the
    colorway or seed axis share a ``layout_id`` (they are the same layout), while
    a change to placement, geometry or symmetry yields a different id. Used for
    de-dup and diversity ranking by the candidate orchestrator.
    """
    payload = intent.model_dump(
        mode="json", exclude={"seed", "colorways", "palette"}
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def stable_hash(text: str) -> int:
    """Deterministic integer hash of ``text`` (sha256, full digest as an int).

    The same sha256 algorithm as ``layout_id_for`` / ``facets.variant_group_key`` /
    ``adapters.base.cache_key`` — but a SEPARATE helper, not a reuse of ``layout_id_for``
    (which hashes a whole intent). Defining it once here keeps variant selection stable
    across processes and platforms (spec §7.1). Returns the full digest as an int so the
    caller can take ``% len(pool)``; never truncated.
    """
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)


def select_variant(pool_ids: list[str], variant_group: str, seed: int) -> str:
    """Pick one variant from ``pool_ids`` as a pure function of (variant_group, seed).

    The pool is sorted by ``motif_id`` first, so the choice is invariant to the order
    the store returned rows in (spec §9.7). ``seed``-only changes yield a different
    variant when the pool has >= 2 entries; randomness is forbidden (D7).
    """
    if not pool_ids:
        raise ValueError("select_variant requires a non-empty pool")
    pool = sorted(pool_ids)
    return pool[stable_hash(f"{variant_group}:{seed}") % len(pool)]


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
