"""Product generate route: a thin adapter over the engine candidate orchestrator.

Session 6 is intent-direct (the stub builder passes a supplied ``intent`` through);
the LLM/image builder is session 7. Diversification, ranking and de-dup live in
``app.engine.candidates`` so the determinism contract stays in the engine layer.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException

from app.api.schemas.generate import (
    CandidateResponse,
    GenerateRequest,
    GenerateResponse,
)
from app.adapters.base import AdapterClientError
from app.adapters.embedding import get_default_embedding_client
from app.adapters.image import build_intent as image_build_intent
from app.adapters.llm import build_intent as llm_build_intent
from app.adapters.motif_resolver import resolve_motifs
from app.adapters.recraft import get_default_recraft_client
from app.adapters.registry_fingerprint import registry_version_for
from app.core.observability import get_request_id, log_metrics
from app.engine.candidates import SOURCE_FIDELITY_VECTOR, generate_candidates
from app.motifs.store import get_default_store
from app.validate.intent import IntentInvalid

router = APIRouter(prefix="/generate", tags=["generate"])

# Product-surface fields that are ignored when a raw `intent` is supplied directly
# (the intent is then authoritative); they ARE honored on the prompt/image paths.
_INTENT_DIRECT_IGNORED = ("prompt", "reference_image", "canvas", "palette")

_GENERATE_DESCRIPTION = """
`intent`, `reference_image`, `prompt` 중 하나로 seamless SVG candidate를 생성합니다.

입력 우선순위는 `intent > reference_image > prompt`입니다. `intent`를 직접 보내면
외부 adapter 없이 deterministic engine에 그대로 전달됩니다. `prompt`와
`reference_image` 경로는 LLM/Recraft/image adapter 설정이 필요하며, 미설정 시
`502`가 반환될 수 있습니다.

`candidate_count`는 `1..8` 범위입니다. 같은 `intent`, `seed`, `colorway` 조합은
동일 SVG를 반환합니다.
""".strip()

_INTENT_DIRECT_EXAMPLE = {
    "intent": {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 184231,
        "production": {"method": "digital", "max_colors": 12},
        "palette": {
            "slots": [
                {"id": "ground", "hex": "#10243a", "name": "navy"},
                {"id": "accent", "hex": "#ef8a7a"},
            ]
        },
        "colorways": [
            {
                "id": "default",
                "name": "default",
                "mapping": {"ground": "#10243a", "accent": "#ef8a7a"},
            },
            {
                "id": "inverse",
                "name": "inverse",
                "mapping": {"ground": "#ef8a7a", "accent": "#10243a"},
            },
        ],
        "layers": [
            {
                "id": "ground",
                "type": "background",
                "z_order": 0,
                "params": {"color": "ground"},
            },
            {
                "id": "stripe_base",
                "type": "stripe",
                "z_order": 1,
                "params": {
                    "angle": -36.87,
                    "period_mm": 9.6,
                    "bands": [
                        {"offset_mm": 0, "width_mm": 4.8, "color": "accent"}
                    ],
                },
            },
        ],
    },
    "candidate_count": 2,
    "seed": 999,
}

_GENERATE_OPENAPI_EXAMPLES = {
    "intent_direct": {
        "summary": "Intent 직접 입력",
        "description": "외부 adapter 없이 deterministic engine에 직접 전달되는 intent 요청 예시입니다.",
        "value": _INTENT_DIRECT_EXAMPLE,
    },
    "intent_colorway": {
        "summary": "Colorway 선택",
        "description": "`intent.colorways`에 있는 colorway id를 지정해 동일 intent의 색상 변형을 생성합니다.",
        "value": {
            **_INTENT_DIRECT_EXAMPLE,
            "colorway": "inverse",
            "candidate_count": 1,
        },
    },
    "prompt_adapter": {
        "summary": "Prompt 입력",
        "description": "LLM/Recraft adapter 설정이 있을 때 사용하는 제품 surface 요청 예시입니다.",
        "value": {
            "prompt": "navy paisley tie with small gold accents",
            "canvas": {"tile_mm": 48, "dpi": 300},
            "candidate_count": 4,
            "seed": 184231,
        },
    },
}


def _run_adapter(call):
    """Invoke an adapter, mapping its failures to HTTP status: IntentInvalid -> 422
    (semantic, after the adapter's own one re-prompt), client/decoding failure -> 502."""
    try:
        return call()
    except IntentInvalid as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from None
    except AdapterClientError as exc:
        raise HTTPException(status_code=502, detail=[str(exc)]) from None


@router.post(
    "",
    response_model=GenerateResponse,
    summary="Generate seamless SVG candidates",
    description=_GENERATE_DESCRIPTION,
)
async def generate_candidate(
    request: Annotated[
        GenerateRequest,
        Body(openapi_examples=_GENERATE_OPENAPI_EXAMPLES),
    ],
) -> GenerateResponse:
    # Resolve the intent: direct > reference_image > prompt. The adapters live outside
    # the engine; they freeze/cache their (non-deterministic) output so the pipeline
    # below stays deterministic. Adapter IntentInvalid -> 422 (after its own one-shot
    # re-prompt); a missing/failed external client -> 5xx.
    warnings: list[str] = []
    source_fidelity = SOURCE_FIDELITY_VECTOR
    motif_specs: list[dict] = []

    if request.intent is not None:
        intent_raw = request.intent
        warnings += [
            f"{name} ignored because `intent` was supplied directly"
            for name in _INTENT_DIRECT_IGNORED
            if getattr(request, name) is not None
        ]
    elif request.reference_image is not None:
        adapted = _run_adapter(
            lambda: image_build_intent(request.reference_image, canvas=request.canvas)
        )
        intent_raw, source_fidelity = adapted.intent, adapted.source_fidelity
        warnings += adapted.warnings
    elif request.prompt is not None:
        adapted = _run_adapter(
            lambda: llm_build_intent(
                request.prompt, canvas=request.canvas, palette=request.palette
            )
        )
        intent_raw, source_fidelity = adapted.intent, adapted.source_fidelity
        warnings += adapted.warnings
        motif_specs = adapted.motif_specs
    else:
        raise HTTPException(
            status_code=422,
            detail=["one of `intent`, `reference_image`, or `prompt` is required"],
        )

    # S10 glue: resolve each motif spec to a concrete motif_id and inject it into the
    # intent BEFORE the engine sees it (the engine only composes concrete motif ids).
    # Selection is deterministic; miss-path generation is frozen by the adapter cache.
    # IntentInvalid -> 422, generation/client failure -> 502 (same mapping as adapters).
    if motif_specs:
        # Unify the seed: the engine falls back to `intent.seed` when request.seed is
        # None (candidates.py), so variant selection must see the SAME effective seed or
        # the two stages would diverge. `or 0` is intentionally avoided (it would conflate
        # an explicit seed=0 with None).
        effective_seed = (
            request.seed if request.seed is not None else int(intent_raw.get("seed") or 0)
        )
        intent_raw = _run_adapter(
            lambda: resolve_motifs(
                intent_raw,
                motif_specs,
                store=get_default_store(),
                embedding_client=get_default_embedding_client(),
                recraft_client=get_default_recraft_client(),
                seed=effective_seed,
                warnings=warnings,
            )
        )

    # Derive the repro seal once per request from the live curated pool (spec §7.3/D17):
    # the version moves with the pool, so unsaved (prompt, seed) requests stay reproducible
    # within a pool snapshot. Store absent/empty/erroring -> baseline (see helper).
    loop = asyncio.get_event_loop()
    reg_version = await loop.run_in_executor(
        None, registry_version_for, get_default_store()
    )
    started = time.perf_counter()
    try:
        result = generate_candidates(
            intent_raw,
            candidate_count=request.candidate_count,
            seed=request.seed,
            colorway=request.colorway,
            source_fidelity=source_fidelity,
            registry_version=reg_version,
        )
    except IntentInvalid as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from None
    except (AssertionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=[str(exc)]) from None
    generate_ms = round((time.perf_counter() - started) * 1000, 1)

    if not result.candidates:
        raise HTTPException(
            status_code=500, detail=["no candidate could be composed"]
        )

    candidates = [
        CandidateResponse(
            id=rc.id,
            svg=rc.candidate.svg,
            intent=rc.intent.model_dump(mode="json"),
            layout_id=rc.candidate.layout_id,
            source_fidelity=rc.source_fidelity,
            repro=asdict(rc.candidate.repro),
        )
        for rc in result.candidates
    ]
    warnings = warnings + result.warnings

    distinct_layouts = len({rc.candidate.layout_id for rc in result.candidates})
    log_metrics(
        "generate",
        requested=request.candidate_count,
        returned=len(candidates),
        distinct_layouts=distinct_layouts,
        available_strategies=result.available_strategy_count,
        warnings=len(warnings),
        generate_ms=generate_ms,
    )

    return GenerateResponse(
        request_id=get_request_id(),
        candidates=candidates,
        warnings=warnings,
    )
