"""Product API schemas for generation and raster export.

The request mirrors the product surface in ``ARCHITECTURE.md`` (prompt-shaped).
``prompt`` and ``reference_image`` are wired through the session-7 adapters
(``app.adapters.llm`` / ``app.adapters.image``); supplying a raw ``intent`` instead
takes the intent-direct path and the engine diversifies it into ranked candidates.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Product surface (forward-compat; honored from session 7).
    prompt: str | None = None
    # base64 / data-URI image, size-bounded (cheap DoS guard; full upload validation is
    # session 8). ~12M chars ≈ 9 MB decoded.
    reference_image: str | None = Field(default=None, max_length=12_000_000)
    canvas: dict[str, Any] | None = None
    palette: dict[str, Any] | None = None

    # Session-6 stub-builder input: the base intent to diversify.
    intent: dict[str, Any] | None = None

    # Honored this session.
    colorway: str | None = None
    seed: int | None = None
    candidate_count: int = Field(default=4, ge=1, le=8)


class ReproResponse(BaseModel):
    """Reproduction metadata: same fields reproduce a byte-identical SVG."""

    engine_version: str
    registry_version: str
    intent_version: int
    colorway_id: str
    seed: int
    layout_id: str | None = None


class CandidateResponse(BaseModel):
    id: str
    svg: str
    intent: dict[str, Any]  # the resolved variant intent this candidate composed
    layout_id: str
    source_fidelity: str
    repro: ReproResponse  # extension beyond the architecture shape: determinism evidence


class GenerateResponse(BaseModel):
    request_id: str
    candidates: list[CandidateResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Size cap is a cheap pre-renderer DoS guard; full SVG sanitization is session 8.
    svg: str = Field(max_length=2_000_000)
    format: Literal["png", "tiff"] = "png"
    dpi: int = 300
    width_mm: float = Field(gt=0)
    height_mm: float | None = Field(default=None, gt=0)
