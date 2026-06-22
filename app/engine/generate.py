"""Intent-to-candidate generation pipeline.

Wires the deterministic engine end to end:
``intent -> validate -> compose -> seamless guard -> SVG candidate (+ repro meta)``.

Rasterization is intentionally NOT performed here: ``generate`` stays pure and
byte-deterministic (same intent + seed + colorway -> identical SVG). The raster
``edge_seam`` regression guard is a separate, renderer-pinned validation step
exercised by the tests, not the generation path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import REGISTRY_VERSION
from app.engine.composition import compose
from app.engine.determinism import ReproMeta, layout_id_for
from app.engine.seamless import assert_seamless_invariants
from app.validate.intent import validate_intent


@dataclass(frozen=True)
class Candidate:
    """A generated SVG plus its reproduction metadata."""

    svg: str
    repro: ReproMeta
    warnings: list[str] = field(default_factory=list)
    layout_id: str | None = None


def generate(
    raw,
    *,
    colorway_id: str = "default",
    seed: int | None = None,
    registry_version: str = REGISTRY_VERSION,
) -> Candidate:
    """Generate a single seamless SVG candidate from a raw or parsed intent.

    ``seed`` overrides the intent's seed in the reproduction metadata when given;
    otherwise the intent's own seed is recorded. ``registry_version`` is stamped into
    the repro meta as-is (the route derives it from the curated pool); it defaults to
    the baseline constant so direct engine callers stay store-free.
    """
    result = validate_intent(raw)
    effective_seed = result.intent.seed if seed is None else seed
    intent = result.intent.model_copy(update={"seed": effective_seed})
    assert_seamless_invariants(intent)
    svg = compose(intent, result.palette, colorway_id)
    layout_id = layout_id_for(intent)
    repro = ReproMeta.build(
        intent_version=intent.intent_version,
        seed=effective_seed,
        colorway_id=colorway_id,
        layout_id=layout_id,
        registry_version=registry_version,
    )
    return Candidate(
        svg=svg,
        repro=repro,
        warnings=list(result.warnings),
        layout_id=layout_id,
    )
