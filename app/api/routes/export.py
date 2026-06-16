"""Export endpoint with format negotiation. Phase 1 serves SVG only;
png/tiff return 501 until the resvg raster pipeline lands (Phase 2)."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.deps import get_store
from app.api.schemas.common import ExportFormat
from app.render.svg import render_document

router = APIRouter(prefix="/patterns", tags=["export"])

SVG_MEDIA_TYPE = "image/svg+xml"


@router.get("/{pattern_id}/export")
def export_pattern(
    pattern_id: str,
    format: ExportFormat = ExportFormat.svg,
    dpi: int = 300,
    width_mm: float | None = None,
    store=Depends(get_store),
) -> Response:
    pattern = store.get(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="pattern not found")

    if format is ExportFormat.svg:
        svg = render_document(pattern, doc_mm=width_mm)
        return Response(content=svg, media_type=SVG_MEDIA_TYPE)

    raise HTTPException(
        status_code=501,
        detail=f"{format.value} export is not implemented yet (Phase 2: resvg)",
    )
