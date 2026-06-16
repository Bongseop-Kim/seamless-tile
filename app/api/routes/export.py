"""Export endpoint with format negotiation: SVG (vector) or PNG/TIFF raster.

Raster goes through resvg (renders SVG filters faithfully) then Pillow stamps
physical DPI. Returns 503 if no renderer binary is installed."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.api.deps import get_store
from app.api.schemas.common import ExportFormat
from app.core.config import get_settings
from app.render import raster
from app.render.svg import document_dimensions_mm, render_document

router = APIRouter(prefix="/patterns", tags=["export"])

SVG_MEDIA_TYPE = "image/svg+xml"


@router.get("/{pattern_id}/export")
def export_pattern(
    pattern_id: str,
    format: ExportFormat = ExportFormat.svg,
    dpi: int = Query(300, ge=10, le=4000),
    width_mm: float | None = Query(None, gt=0),
    store=Depends(get_store),
) -> Response:
    pattern = store.get(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="pattern not found")

    svg = render_document(pattern, doc_mm=width_mm)
    if format is ExportFormat.svg:
        return Response(content=svg, media_type=SVG_MEDIA_TYPE)

    settings = get_settings()
    if dpi > settings.max_dpi:
        raise HTTPException(
            status_code=422, detail=f"dpi exceeds max_dpi ({settings.max_dpi})"
        )
    if width_mm is not None and width_mm > settings.max_tile_mm:
        raise HTTPException(
            status_code=422,
            detail=f"width_mm exceeds max_tile_mm ({settings.max_tile_mm})",
        )
    binary = raster.find_renderer(settings.renderer_bin)
    if not binary:
        raise HTTPException(
            status_code=503,
            detail="no SVG renderer installed; run: brew install librsvg",
        )
    width, height = document_dimensions_mm(pattern, width_mm)
    try:
        data, media_type = raster.rasterize(
            svg, format.value, dpi, width, height_mm=height, binary=binary
        )
    except raster.RasterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=data, media_type=media_type)
