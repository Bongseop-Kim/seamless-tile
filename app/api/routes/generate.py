"""Product generate route: a thin adapter over the engine candidate orchestrator.

Session 6 is intent-direct (the stub builder passes a supplied ``intent`` through);
the LLM/image builder is session 7. Diversification, ranking and de-dup live in
``app.engine.candidates`` so the determinism contract stays in the engine layer.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from functools import partial
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException

from app.api.schemas.generate import (
    CandidateResponse,
    GenerateRequest,
    GenerateResponse,
)
from app.adapters.base import AdapterClientError, cache_key
from app.adapters.embedding import get_default_embedding_client
from app.adapters.image import build_intent as image_build_intent
from app.adapters.image import decode_and_clean as image_decode_and_clean
from app.adapters.llm import build_intents as llm_build_intents
from app.adapters.motif_resolver import resolve_motifs
from app.adapters.recraft import get_default_recraft_client
from app.adapters.registry_fingerprint import registry_version_for
from app.core.config import get_settings
from app.core.observability import get_request_id, log_metrics
from app.engine.candidates import SOURCE_FIDELITY_VECTOR, generate_candidate_set
from app.logs.generation_log import GenerationLogRow, insert_generation_log
from app.motifs.store import get_default_store
from app.storage.preview import make_preview, preview_configured
from app.validate.intent import IntentInvalid

router = APIRouter(prefix="/generate", tags=["generate"])

# Product-surface fields that are ignored when a raw `intent` is supplied directly
# (the intent is then authoritative); they ARE honored on the prompt/image paths.
_INTENT_DIRECT_IGNORED = ("prompt", "reference_image", "images", "canvas", "palette")

# Process-local LRU: cache_key(request + reg_version) -> (candidates_payload, warnings).
# candidates_payload is [(id, png_url), ...]; request_id is intentionally NOT stored so a
# cache hit always re-stamps the caller's own (fresh) request_id. Mirrors the embedding LRU.
_RESPONSE_CACHE: "OrderedDict[str, tuple[list[tuple[str, str]], list[str]]]" = OrderedDict()


def reset_response_cache() -> None:
    """Clear the in-process generate response cache (test isolation / ops)."""
    _RESPONSE_CACHE.clear()

_GENERATE_DESCRIPTION = """
`intent`, `reference_image`, `prompt` 중 하나로 seamless SVG candidate를 생성합니다.

입력 우선순위는 `intent > reference_image > prompt`입니다. `intent`를 직접 보내면
외부 adapter 없이 deterministic engine에 그대로 전달됩니다. `prompt`와
`reference_image` 경로는 LLM/Recraft/image adapter 설정이 필요하며, 미설정 시
`502`가 반환될 수 있습니다.

`candidate_count`는 `1..8` 범위입니다. 같은 `intent`, `seed`, `colorway` 조합은
결정론적으로 동일한 결과를 만듭니다.

각 candidate는 `id`와 `png_url`(Supabase Storage에 렌더된 미리보기 PNG의 public URL)만
반환합니다. SVG 원본과 intent·repro 메타데이터는 응답에 포함되지 않고 서버사이드 로그에
보존됩니다. 미리보기 storage 미설정 시 `png_url`은 `null`이며 `warnings`에 안내가 추가됩니다.
""".strip()

_INTENT_DIRECT_EXAMPLE = {
    "intent": {
        "intent_version": 1,
        "canvas": {"tile_mm": 48, "dpi": 300},
        "seed": 184231,
        "production": {"method": "print", "max_colors": 12},
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
    summary="Generate seamless tile candidates (PNG preview URLs)",
    description=_GENERATE_DESCRIPTION,
)
async def generate_candidate(
    request: Annotated[
        GenerateRequest,
        Body(openapi_examples=_GENERATE_OPENAPI_EXAMPLES),
    ],
    background_tasks: BackgroundTasks,
) -> GenerateResponse:
    # Resolve the intent: direct > reference_image > prompt. The adapters live outside
    # the engine; they freeze/cache their (non-deterministic) output so the pipeline
    # below stays deterministic. Adapter IntentInvalid -> 422 (after its own one-shot
    # re-prompt); a missing/failed external client -> 5xx.
    warnings: list[str] = []
    source_fidelity = SOURCE_FIDELITY_VECTOR
    input_type = "intent"
    # Decoded, validated, metadata-stripped bytes for the multi-image chat path; passed
    # to both the LLM (multimodal binding) and the resolver (vectorize). None elsewhere.
    cleaned_images: list[bytes] | None = None

    # Cache short-circuit BEFORE any adapter/engine/render work. The repro seal moves with
    # the reusable motif pool, so it is part of the key (pool change -> auto-invalidation); it is a
    # pure function of the pool, memoized per (store, epoch), so calling it on a hit is cheap.
    settings = get_settings()
    loop = asyncio.get_event_loop()
    reg_version = await loop.run_in_executor(
        None, registry_version_for, get_default_store()
    )
    key = None
    if settings.generate_cache_size:
        key = cache_key(
            {"k": "generate", "request": request.model_dump(), "reg": reg_version}
        )
        hit = _RESPONSE_CACHE.get(key)
        if hit is not None:
            _RESPONSE_CACHE.move_to_end(key)
            cached_candidates, cached_warnings = hit
            log_metrics(
                "generate",
                cache="hit",
                returned=len(cached_candidates),
                warnings=len(cached_warnings),
            )
            return GenerateResponse(
                request_id=get_request_id(),
                candidates=[
                    CandidateResponse(id=cid, png_url=url)
                    for cid, url in cached_candidates
                ],
                warnings=cached_warnings,
            )

    # Resolve the input into a list of (intent, motif_specs) "designs". Only the prompt
    # path produces more than one; intent-direct/image are single-design.
    if request.intent is not None:
        designs: list[tuple[dict, list[dict]]] = [(request.intent, [])]
        warnings += [
            f"{name} ignored because `intent` was supplied directly"
            for name in _INTENT_DIRECT_IGNORED
            if getattr(request, name) is not None
        ]
    elif request.images:
        # Multi-image chat path: prompt + N images go to the multimodal LLM together; it
        # binds each image to a role (style -> palette, motif -> vectorized). Decode +
        # validate + strip every image ONCE here, before any bytes leave the box.
        input_type = "reference_images"
        cleaned_images = _run_adapter(
            lambda: [image_decode_and_clean(s) for s in request.images]
        )
        adapted_list = await asyncio.to_thread(
            _run_adapter,
            lambda: llm_build_intents(
                request.prompt or "",
                canvas=request.canvas,
                palette=request.palette,
                images=cleaned_images,
                use_cache=False,  # chat surface: re-ask -> fresh designs (see prompt path)
            ),
        )
        source_fidelity = adapted_list[0].source_fidelity
        for adapted in adapted_list:
            warnings += adapted.warnings
        designs = [(a.intent, a.motif_specs) for a in adapted_list]
    elif request.reference_image is not None:
        input_type = "reference_image"
        adapted = await asyncio.to_thread(
            _run_adapter,
            lambda: image_build_intent(request.reference_image, canvas=request.canvas),
        )
        source_fidelity = adapted.source_fidelity
        warnings += adapted.warnings
        designs = [(adapted.intent, adapted.motif_specs)]
    elif request.prompt is not None:
        input_type = "prompt"
        adapted_list = await asyncio.to_thread(
            _run_adapter,
            lambda: llm_build_intents(
                request.prompt,
                canvas=request.canvas,
                palette=request.palette,
                # 채팅 표면: 같은 프롬프트를 다시 보내도 temp>0로 새로 작성해 다른 디자인을
                # 내준다("다시 만들어줘"). 어댑터 캐시는 직접-호출/테스트/repro용으로 보존.
                use_cache=False,
            ),
        )
        source_fidelity = adapted_list[0].source_fidelity
        for adapted in adapted_list:
            warnings += adapted.warnings
        designs = [(a.intent, a.motif_specs) for a in adapted_list]
    else:
        raise HTTPException(
            status_code=422,
            detail=["one of `intent`, `images`, `reference_image`, or `prompt` is required"],
        )

    # S10 glue: resolve each design's motif specs to concrete motif_ids and inject them
    # BEFORE the engine sees the intent (the engine only composes concrete motif ids).
    # Each design carries its own specs keyed by its own layer ids. Deterministic;
    # miss-path generation is frozen by the adapter cache. IntentInvalid -> 422,
    # generation/client failure -> 502 (same mapping as adapters).
    base_raws: list[dict] = []
    for design_intent, design_specs in designs:
        if design_specs:
            # Unify the seed: the engine falls back to `intent.seed` when request.seed is
            # None (candidates.py), so variant selection must see the SAME effective seed
            # or the two stages would diverge. `or 0` is intentionally avoided (it would
            # conflate an explicit seed=0 with None).
            effective_seed = (
                request.seed
                if request.seed is not None
                else int(design_intent.get("seed") or 0)
            )
            design_intent = _run_adapter(
                lambda di=design_intent, ds=design_specs, es=effective_seed: resolve_motifs(
                    di,
                    ds,
                    store=get_default_store(),
                    embedding_client=get_default_embedding_client(),
                    recraft_client=get_default_recraft_client(),
                    seed=es,
                    images=cleaned_images,
                    warnings=warnings,
                )
            )
        base_raws.append(design_intent)

    # reg_version (the repro seal, spec §7.3/D17) was derived once at the top of the handler
    # for the cache key and is reused here.
    started = time.perf_counter()
    try:
        result = generate_candidate_set(
            base_raws,
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

    warnings = warnings + result.warnings
    distinct_layouts = len({rc.candidate.layout_id for rc in result.candidates})
    request_id = get_request_id()

    # Render each candidate's SVG to a preview PNG and upload it to Storage once, here;
    # the response carries only the URL. The SVG source is preserved in the log below
    # (the slimmed response no longer returns svg/intent/repro). Renders run in parallel
    # and are best-effort per candidate — a render/upload miss degrades to png_url=null.
    render_started = time.perf_counter()
    if preview_configured():
        rendered = await asyncio.gather(
            *(
                loop.run_in_executor(
                    None,
                    partial(
                        make_preview,
                        rc.candidate.svg,
                        tile_mm=rc.intent.canvas.tile_mm,
                        dpi=settings.preview_dpi,
                        path=f"{request_id}/{rc.id}.png",
                    ),
                )
                for rc in result.candidates
            ),
            return_exceptions=True,
        )
        png_urls: list[str | None] = []
        for rc, res in zip(result.candidates, rendered, strict=True):
            if isinstance(res, BaseException):
                warnings.append(f"preview unavailable for candidate {rc.id}: {res}")
                png_urls.append(None)
            else:
                png_urls.append(res)
    else:
        warnings.append("preview storage not configured; png_url is null")
        png_urls = [None] * len(result.candidates)
    render_ms = round((time.perf_counter() - render_started) * 1000, 1)

    # Intent-level warnings (gamut, dpi clamp, ...) are emitted once per candidate by the
    # per-candidate validation, so identical messages pile up; dedupe order-preserving.
    # Per-candidate warnings stay distinct (they name the candidate id).
    warnings = list(dict.fromkeys(warnings))

    candidates = [
        CandidateResponse(id=rc.id, png_url=url)
        for rc, url in zip(result.candidates, png_urls, strict=True)
    ]

    # Cache only fully-successful renders: a None url means preview was unconfigured or a
    # render/upload failed (and that failure warning is nondeterministic), so such responses
    # must not be served back. ponytail: no single-flight on concurrent first-misses — each
    # uploads to its own request_id path (x-upsert idempotent), last write wins here, and the
    # served URL is always valid; worst case one orphaned PNG. Per-key asyncio.Lock if it ever matters.
    if key is not None and all(url is not None for url in png_urls):
        _RESPONSE_CACHE[key] = (
            [
                (rc.id, url)
                for rc, url in zip(result.candidates, png_urls, strict=True)
            ],
            warnings,
        )
        _RESPONSE_CACHE.move_to_end(key)
        if len(_RESPONSE_CACHE) > settings.generate_cache_size:
            _RESPONSE_CACHE.popitem(last=False)

    # Best-effort logging off the hot path (no-op without SUPABASE_DB_URL); preserves
    # the SVG + intent/repro that the response dropped.
    background_tasks.add_task(
        insert_generation_log,
        GenerationLogRow(
            request_id=request_id,
            input_type=input_type,
            status="partial" if any("partial" in w for w in warnings) else "success",
            prompt=request.prompt,
            has_reference_image=request.reference_image is not None,
            reference_image_bytes=(
                len(request.reference_image) if request.reference_image else None
            ),
            colorway=request.colorway,
            seed=request.seed,
            candidate_count_requested=request.candidate_count,
            candidate_count_returned=len(candidates),
            distinct_layouts=distinct_layouts,
            available_strategies=result.available_strategy_count,
            engine_version=result.candidates[0].candidate.repro.engine_version,
            registry_version=reg_version,
            intent={"designs": base_raws},
            candidates=[
                {
                    "id": rc.id,
                    "design_index": rc.design_index,
                    "layout_id": rc.candidate.layout_id,
                    "source_fidelity": rc.source_fidelity,
                    "colorway_id": rc.candidate.repro.colorway_id,
                    "seed": rc.candidate.repro.seed,
                    "svg": rc.candidate.svg,
                    "png_url": url,
                }
                for rc, url in zip(result.candidates, png_urls, strict=True)
            ],
            warnings=warnings,
            generate_ms=generate_ms,
            render_ms=render_ms,
        ),
    )

    log_metrics(
        "generate",
        requested=request.candidate_count,
        returned=len(candidates),
        distinct_layouts=distinct_layouts,
        available_strategies=result.available_strategy_count,
        warnings=len(warnings),
        generate_ms=generate_ms,
        render_ms=render_ms,
    )

    return GenerateResponse(
        request_id=request_id,
        candidates=candidates,
        warnings=warnings,
    )
