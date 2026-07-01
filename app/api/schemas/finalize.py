"""Finalize (fabric texture render) API schema.

``POST /api/v1/finalize`` takes an approved candidate's resolved ``intent`` (plus the
active colorway) and re-composes it deterministically, then composites a bundled
tileable weave photo onto the raster to make a "cloth" PNG. Input is the intent rather
than a looked-up SVG: composition is deterministic (byte-identical) and the intent is
also needed for per-region (per color-slot) texturing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FinalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: dict[str, Any]
    # Colorway id to render; defaults to the intent's ``default`` colorway.
    colorway_id: str | None = None
    # Override the intent's ``production.method``; None => use the intent's value.
    #   print     -> a single twill weave over the whole tile (material_map rejected).
    #   yarn_dyed -> per-region weave mix via material_map.
    production_method: Literal["print", "yarn_dyed"] | None = None
    # Weave applied uniformly (print) or as the per-region fallback (yarn_dyed):
    # solid | twill-0 | twill-45 | herringbone (print requires a twill-*).
    weave: str = "twill-45"
    # yarn_dyed only: color-slot id -> weave. Slots absent here fall back to ``weave``.
    # An empty/omitted map yields the uniform result.
    material_map: dict[str, str] | None = None
    # Raster resolution; defaults to ``fabric_dpi``, capped by ``max_dpi``.
    dpi: int | None = Field(default=None, gt=0)
    # Weave visibility: amplifies the texture's darkening (1.0 = raw photo, higher = more
    # pronounced). None => DEFAULT_TEXTURE_STRENGTH.
    texture_strength: float | None = Field(default=None, ge=0)
    # yarn_dyed only: emboss color-slot boundaries so motifs read as raised woven threads.
    # 1.0 = default bevel, 0 = flat. None => DEFAULT_RELIEF_STRENGTH. Ignored for print.
    relief_strength: float | None = Field(default=None, ge=0)


class FinalizeResponse(BaseModel):
    request_id: str
    # Public Supabase Storage URL of the textured PNG; None when storage is unconfigured
    # (see ``warnings``).
    image_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
