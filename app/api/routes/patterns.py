"""Pattern definition endpoints: create (per type) and fetch as SVG."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.deps import get_store
from app.api.schemas.check import CheckRequest
from app.api.schemas.dot import DotRequest
from app.api.schemas.herringbone import HerringboneRequest
from app.api.schemas.stripe import StripeRequest
from app.domain.colorway import Colorway
from app.domain.pattern import Pattern
from app.patterns.check import CheckPattern
from app.patterns.dot import DotPattern
from app.patterns.herringbone import HerringbonePattern
from app.patterns.stripe import StripePattern
from app.render.svg import render_document

router = APIRouter(prefix="/patterns", tags=["patterns"])

SVG_MEDIA_TYPE = "image/svg+xml"


def _store_and_respond(pattern: Pattern, store) -> dict[str, str]:
    pattern_id = uuid.uuid4().hex
    store[pattern_id] = pattern
    return {"id": pattern_id, "svg": render_document(pattern)}


@router.post("/stripe")
def create_stripe(req: StripeRequest, store=Depends(get_store)) -> dict[str, str]:
    pattern = StripePattern(
        tile_mm=req.tile_mm,
        colorway=Colorway(req.colors),
        widths_mm=req.widths_mm,
        angle=req.angle,
    )
    return _store_and_respond(pattern, store)


@router.post("/check")
def create_check(req: CheckRequest, store=Depends(get_store)) -> dict[str, str]:
    pattern = CheckPattern(
        tile_mm=req.tile_mm,
        colorway=Colorway(req.colors),
        widths_mm=req.widths_mm,
        opacity=req.opacity,
    )
    return _store_and_respond(pattern, store)


@router.post("/dot")
def create_dot(req: DotRequest, store=Depends(get_store)) -> dict[str, str]:
    pattern = DotPattern(
        spacing_mm=req.spacing_mm,
        radius_mm=req.radius_mm,
        colorway=Colorway(req.colors),
        repeat=req.repeat,
    )
    return _store_and_respond(pattern, store)


@router.post("/herringbone")
def create_herringbone(
    req: HerringboneRequest, store=Depends(get_store)
) -> dict[str, str]:
    pattern = HerringbonePattern(
        tile_mm=req.tile_mm,
        colorway=Colorway(req.colors),
        stroke_mm=req.stroke_mm,
        pitch_mm=req.pitch_mm,
    )
    return _store_and_respond(pattern, store)


@router.get("/{pattern_id}")
def get_pattern(pattern_id: str, store=Depends(get_store)) -> Response:
    pattern = store.get(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail="pattern not found")
    return Response(content=render_document(pattern), media_type=SVG_MEDIA_TYPE)
