"""Export route: rasterize a candidate SVG to PNG/TIFF.

SVG is the single source of truth; this is a convenience for raster deliverables.
Client-side guards (dpi/size) return 400; a missing or failing renderer is an
upstream failure and returns 5xx.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from app.api.schemas.generate import ExportRequest
from app.core.config import get_settings
from app.core.observability import log_metrics
from app.engine.units import mm_to_px
from app.render.raster import MAX_DIMENSION_PX, RasterError, rasterize
from app.render.sanitize import SanitizeError, scrub_svg

router = APIRouter(prefix="/export", tags=["export"])


@router.post("")
def export_candidate(request: ExportRequest) -> Response:
    settings = get_settings()

    if not (0 < request.dpi <= settings.max_dpi):
        raise HTTPException(
            status_code=400,
            detail=[f"dpi {request.dpi} out of range (1..{settings.max_dpi})"],
        )
    for label, value in (("width_mm", request.width_mm), ("height_mm", request.height_mm)):
        if value is not None and value > settings.max_tile_mm:
            raise HTTPException(
                status_code=400,
                detail=[f"{label} {value} exceeds max {settings.max_tile_mm}"],
            )

    # The mm/dpi limits can still multiply into a raster past the pixel cap; reject that
    # as a client error here rather than letting the renderer raise (which is a 5xx).
    height_mm = request.width_mm if request.height_mm is None else request.height_mm
    width_px = mm_to_px(request.width_mm, request.dpi)
    height_px = mm_to_px(height_mm, request.dpi)
    if width_px > MAX_DIMENSION_PX or height_px > MAX_DIMENSION_PX:
        raise HTTPException(
            status_code=400,
            detail=[
                f"raster {width_px}x{height_px}px exceeds max {MAX_DIMENSION_PX}px; "
                "reduce dpi or size"
            ],
        )

    # Client-supplied SVG is untrusted: validate against the allowlist AND re-serialize
    # (scrub) so comments/PI/foreign nodes can't reach the renderer; blocks external
    # href fetch / injection.
    try:
        safe_svg = scrub_svg(request.svg)
    except SanitizeError as exc:
        raise HTTPException(status_code=400, detail=[f"unsafe svg: {exc}"]) from None

    try:
        data, content_type = rasterize(
            safe_svg,
            request.format,
            request.dpi,
            request.width_mm,
            request.height_mm,
            binary=settings.renderer_bin,
        )
    except RasterError as exc:
        raise HTTPException(status_code=502, detail=[str(exc)]) from None

    log_metrics(
        "export", format=request.format, dpi=request.dpi, bytes=len(data)
    )
    return Response(content=data, media_type=content_type)
