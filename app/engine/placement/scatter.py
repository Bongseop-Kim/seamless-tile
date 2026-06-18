"""Scatter placement: seed-deterministic torus distributions.

Two sub-modes share this file (per ARCHITECTURE.md):

- ``poisson``: blue-noise dart-throwing with a torus minimum distance. Determinism is
  guaranteed by drawing from a single ``seeded_rng(seed)`` in a fixed candidate order
  (no global RNG, no time/hash dependence) -> byte-identical output for a given seed.
- ``sateen``: an N-end satin step grid ``(i*cell, (i*step % N)*cell)``. With
  ``gcd(step, N) == 1`` the rows form a permutation, so no two points share a row or
  column (alignment count 0). Deterministic, no RNG.
"""

from __future__ import annotations

import math

from app.engine.determinism import seeded_rng
from app.engine.intent import Placement
from app.engine.placement.path_following import Instance

# Candidate budget per accepted point for dart-throwing. Fixed so the RNG path (and
# thus the output) is reproducible from the seed alone.
_ATTEMPTS_PER_TARGET = 30

# Densest disk packing (hexagonal) area efficiency = pi/(2*sqrt(3)); the disk-center
# density factor used for the capacity bound is sqrt(3)/2.
_HEX_PACKING_FACTOR = math.sqrt(3) / 2


def _torus_dist(ax: float, ay: float, bx: float, by: float, tile_mm: float) -> float:
    dx = abs(ax - bx)
    dy = abs(ay - by)
    dx = min(dx, tile_mm - dx)
    dy = min(dy, tile_mm - dy)
    return math.hypot(dx, dy)


def _place_poisson(placement: Placement, tile_mm: float, seed: int) -> list[Instance]:
    spec = placement.scatter
    if spec is None:
        raise ValueError("poisson scatter placement requires a `scatter` spec")
    if spec.min_dist_mm is None:
        raise ValueError("poisson scatter placement requires `min_dist_mm`")
    min_dist = spec.min_dist_mm
    rng = seeded_rng(seed)

    # Hex-packing upper bound on how many disks of radius min_dist/2 fit on the torus.
    capacity = max(
        1, int((tile_mm * tile_mm) / (min_dist * min_dist * _HEX_PACKING_FACTOR))
    )
    target = spec.count if spec.count is not None else capacity
    max_attempts = target * _ATTEMPTS_PER_TARGET

    pts: list[tuple[float, float]] = []
    for _ in range(max_attempts):
        x = rng.random() * tile_mm
        y = rng.random() * tile_mm
        if all(_torus_dist(x, y, px, py, tile_mm) >= min_dist for px, py in pts):
            pts.append((x, y))
            if len(pts) >= target:
                break
    return [Instance(x, y, 0.0) for x, y in pts]


def _place_sateen(placement: Placement, tile_mm: float) -> list[Instance]:
    spec = placement.scatter
    if spec is None:
        raise ValueError("sateen scatter placement requires a `scatter` spec")
    if spec.sateen_n is None:
        raise ValueError("sateen scatter placement requires `sateen_n`")
    n = spec.sateen_n
    step = spec.sateen_step if spec.sateen_step is not None else 1
    cell = tile_mm / n
    return [Instance(i * cell, ((i * step) % n) * cell, 0.0) for i in range(n)]


def place_scatter(placement: Placement, tile_mm: float, seed: int) -> list[Instance]:
    spec = placement.scatter
    if spec is None:
        raise ValueError("scatter placement requires a `scatter` spec")
    if spec.mode == "sateen":
        return _place_sateen(placement, tile_mm)
    return _place_poisson(placement, tile_mm, seed)
