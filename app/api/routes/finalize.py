"""Finalize route: turn an approved candidate into a fabric-textured PNG.

Free, local, deterministic (no external model/API). The approved candidate's ``intent``
is re-composed and rasterized, a bundled tileable weave is composited on, and the result
is uploaded to Storage (best-effort). Session 16's finalize node calls the same logic.
"""

from __future__ import annotations

import asyncio
import hashlib
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException

from app.api.schemas.finalize import FinalizeRequest, FinalizeResponse
from app.core.observability import get_request_id, log_metrics
from app.render.fabric import FabricError, render_fabric
from app.storage.preview import preview_configured, upload_png
from app.validate.intent import IntentInvalid

router = APIRouter(prefix="/finalize", tags=["finalize"])

_FINALIZE_DESCRIPTION = """
승인된 후보의 `intent`(+`colorway_id`)를 결정론적으로 재합성해 래스터화한 뒤, 번들 tileable
원단 텍스처를 Pillow로 합성해 "천 느낌" PNG를 만듭니다. 외부 생성 API를 호출하지 않는
무료·로컬 파생 출력이며 seamless를 유지합니다.

`weave`는 assets/fabric의 PNG 파일명입니다(현재: `check | herringbone | jacquard | pindot |
solid | twill-0 | twill-45`; print는 `twill-*`만). `material_map`으로 color slot별 서로 다른
질감을 지정할 수 있습니다(미지정 slot은 `weave`로 폴백, 비우면 균일). 결과 PNG는 Supabase Storage에
업로드되어 `image_url`로 반환되며, storage 미설정 시 `image_url`은 `null`이고 `warnings`에
안내가 추가됩니다.
""".strip()


@router.post(
    "",
    response_model=FinalizeResponse,
    summary="Render an approved candidate as a fabric-textured PNG",
    description=_FINALIZE_DESCRIPTION,
)
async def finalize_candidate(
    request: Annotated[FinalizeRequest, Body()],
) -> FinalizeResponse:
    warnings: list[str] = []
    loop = asyncio.get_event_loop()

    # Render off the event loop. IntentInvalid -> 422 (semantic), FabricError -> 400
    # (bad knob), RasterError bubbles to the global 502 handler (no/failed renderer).
    try:
        png = await loop.run_in_executor(
            None,
            partial(
                render_fabric,
                request.intent,
                colorway_id=request.colorway_id,
                production_method=request.production_method,
                weave=request.weave,
                material_map=request.material_map,
                dpi=request.dpi,
                texture_strength=request.texture_strength,
                relief_strength=request.relief_strength,
            ),
        )
    except IntentInvalid as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from None
    except FabricError as exc:
        raise HTTPException(status_code=400, detail=[str(exc)]) from None

    request_id = get_request_id()

    # Deterministic content-addressed path: same inputs -> same PNG -> same object
    # (x-upsert idempotent). Upload is best-effort; a miss degrades to image_url=null.
    image_url: str | None = None
    if preview_configured():
        path = f"fabric/{hashlib.sha256(png).hexdigest()[:16]}.png"
        try:
            image_url = await loop.run_in_executor(None, partial(upload_png, png, path=path))
        except Exception as exc:  # noqa: BLE001 — storage failure is non-fatal
            warnings.append(f"fabric image upload failed: {exc}")
    else:
        warnings.append("preview storage not configured; image_url is null")

    log_metrics(
        "finalize",
        weave=request.weave,
        regions=len(request.material_map or {}),
        uploaded=image_url is not None,
        warnings=len(warnings),
    )
    return FinalizeResponse(request_id=request_id, image_url=image_url, warnings=warnings)
