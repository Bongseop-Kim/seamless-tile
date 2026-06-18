"""Generate request/response schemas for the engine API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    """Direct-intent generation request."""

    model_config = ConfigDict(extra="forbid")

    intent: dict[str, Any]
    colorway_id: str = "default"
    seed: int | None = None


class ReproResponse(BaseModel):
    """Reproduction metadata returned with a generated candidate."""

    engine_version: str
    registry_version: str
    intent_version: int
    colorway_id: str
    seed: int
    layout_id: str | None = None


class GenerateResponse(BaseModel):
    """Generated SVG candidate response."""

    svg: str
    repro: ReproResponse
    warnings: list[str] = Field(default_factory=list)
    layout_id: str | None = None
