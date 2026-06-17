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

from app.engine.composition import compose
from app.engine.determinism import ReproMeta
from app.engine.seamless import assert_seamless_invariants
from app.validate.intent import validate_intent


@dataclass(frozen=True)
class Candidate:
    """A generated SVG plus its reproduction metadata."""

    svg: str
    repro: ReproMeta
    warnings: list[str] = field(default_factory=list)
    layout_id: str | None = None


def generate(raw, *, colorway_id: str = "default", seed: int | None = None) -> Candidate:
    """Generate a single seamless SVG candidate from a raw or parsed intent.

    ``seed`` overrides the intent's seed in the reproduction metadata when given;
    otherwise the intent's own seed is recorded.
    """
    result = validate_intent(raw)
    assert_seamless_invariants(result.intent)
    svg = compose(result.intent, result.palette, colorway_id)
    effective_seed = result.intent.seed if seed is None else seed
    repro = ReproMeta.build(
        intent_version=result.intent.intent_version,
        seed=effective_seed,
        colorway_id=colorway_id,
    )
    return Candidate(svg=svg, repro=repro, warnings=list(result.warnings))
