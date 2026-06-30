# 아키텍처 - Seamless Tile

`seamless-tile`은 텍스트 prompt 또는 내부 intent JSON을 입력받아 seamless textile tile의 SVG 후보를
생성하는 FastAPI 서비스다. 현재 1차 개발 기준으로 `generate`의 `reference_image` 제품 경로만
미완료 영역이며, 나머지 prompt/intent 생성, motif 해석, SVG 합성, preview/log/export 경로는 현재
코드 기준으로 완료된 아키텍처로 본다.

이 문서는 코드와 운영 경계를 빠르게 파악하기 위한 **canonical architecture map**이다. 세부 구현
히스토리와 세션별 계획은 `docs/plan/*`, `docs/spec/*`에 남아 있어도 현재 계약의 권위 기준이 아니다.
실행 방법은 `README.md`, prompt QA 시나리오는 `docs/prompt-scenarios.md`를 따른다.

`docs/plan/*`은 구현 계획/결정 로그로 보존할 수 있지만, API 필드나 스키마 판단에는 쓰지 않는다.
오래된 plan 문서가 현재 코드와 충돌하면 이 문서와 pydantic schema/test가 우선한다.

## 시스템 컨텍스트

```text
Product / admin client
        |
        | HTTP /api/v1
        v
FastAPI service (this repo)
        |
        +-- Gemini: prompt -> intent JSON + motif specs
        +-- OpenAI embeddings: motif descriptor similarity
        +-- Recraft: detailed/multicolor vector motif generation
        +-- Supabase Postgres: motifs + seamless_generation_logs
        +-- Supabase Storage: generated preview PNGs
        +-- rsvg-convert/resvg: SVG -> PNG/TIFF rasterization
```

경계 규칙:

- LLM/VLM/generator는 최종 SVG tile을 그리지 않는다. intent JSON, motif spec, 재사용 motif SVG까지만 만든다.
- 결정론 엔진이 좌표, 배치, layer 합성, seamless 불변식, 최종 SVG bytes를 소유한다.
- Supabase schema/migration은 React 모노레포가 소유한다. 이 레포는 DB client일 뿐이다.
- SVG가 원본이다. PNG/TIFF와 preview URL은 파생물이다.

## 런타임 컨테이너

```text
app/main.py
  FastAPI app, /api/v1 router mount, lifespan, request-id/error middleware

app/api/
  Public HTTP schemas and routes: generate, export, palettes, health

app/adapters/
  Non-deterministic or external seams: LLM, image, embeddings, Recraft, motif resolver

app/engine/
  Deterministic intent -> SVG engine: validation boundary, placement, composition, seamless, candidates

app/motifs/
  In-memory motif registry, SVG intake normalization, geometry/facets, Postgres motif store

app/render/, app/storage/, app/logs/
  SVG sanitize/rasterization, Supabase preview upload, best-effort generation logging
```

Route layer는 얇게 유지한다. 오래가는 동작은 `app/engine/*`, 외부 불확실성은 `app/adapters/*`,
DB/storage/logging 실패 격리는 `app/motifs/*`, `app/storage/*`, `app/logs/*`에 둔다.

## 내부 엔진 책임

이 서비스의 핵심은 외부 adapter가 만든 intent를 deterministic engine으로 닫는 구조다. 각 내부 엔진은
서로의 구현 세부에 기대지 않고 명확한 계약만 주고받는다.

```text
Intent JSON
  -> Validation Engine
  -> Candidate Engine
  -> Placement Engine
  -> Composition Engine
  -> Seamless Engine
  -> SVG
```

- **Validation Engine** (`app/validate/intent.py`): pydantic 구조 검증 이후 palette/colorway, host/lane,
  placement, commensurability, motif color slot binding, raster limit을 시맨틱하게 검증하고 제한적 repair를
  수행한다.
- **Candidate Engine** (`app/engine/candidates.py`): 하나 이상의 resolved intent를 layout, colorway, seed 축으로
  확장하고, 안정 ranking과 SVG 문자열 de-dup으로 후보 set을 만든다.
- **Placement Engine** (`app/engine/placement/*`): motif instance의 torus 좌표와 rotation을 계산한다.
  `lattice`, `point_set`, `path_following`, `scatter` 전략을 가지며, host 기반 배치는 stripe의 lane 계약만
  참조한다.
- **Composition Engine** (`app/engine/composition.py`): `(z_order, id)`로 layer를 안정 정렬하고, palette/colorway를
  해석해 `<pattern>`, `<defs>`, `<symbol>`, `<use>` 기반의 단일 SVG 문서로 합성한다.
- **Seamless Engine** (`app/engine/seamless.py`): by-construction 불변식을 재단언하고, 경계 밖으로 나가는
  motif instance에 shifted clone을 추가해 tile edge 연속성을 유지한다.
- **Motif Registry/Resolver** (`app/motifs/*`, `app/adapters/motif_resolver.py`): prompt가 만든 motif spec을
  재사용 가능한 concrete `motif_id`로 확정한다. 이 단계는 engine 전에 끝나며, engine은 항상 concrete motif만
  본다.
- **Render/Export Boundary** (`app/render/*`, `app/api/routes/export.py`): SVG를 sanitize/scrub하고 필요할 때만
  PNG/TIFF로 rasterize한다. raster는 파생물이며 engine determinism의 입력이 아니다.

## Generate 흐름

`POST /api/v1/generate`는 `intent`, `reference_image`, `prompt` 중 하나를 받는다. 우선순위는
`intent > reference_image > prompt`다.

```text
GenerateRequest
  -> request/cache key = request body + registry pool fingerprint
  -> 입력 해석:
       intent          -> 그대로 사용, prompt/image/canvas/palette는 무시 경고
       reference_image -> image adapter seam (현재 제품 경로 미완료)
       prompt          -> Gemini designs[] = intent + motif_specs
  -> motif_resolver가 engine 실행 전에 concrete motif_id를 주입
  -> validate_intent + assert_seamless_invariants
  -> generate_candidate_set이 layout, colorway, seed 축으로 후보 생성
  -> compose가 SVG candidates를 결정론적으로 합성
  -> Supabase Storage 설정 시 preview PNG render/upload
  -> slim response 반환: request_id, candidates[].{id,png_url}, warnings[]
  -> background log에 resolved intent, SVG, layout_id, source_fidelity, repro data 저장
```

현재 `reference_image` 상태:

- 구현됨: base64/data-URI 검증, 이미지 크기/포맷 hardening, metadata stripping, 결정론적 median-cut palette
  추출, optional VLM/vectorizer injection seam, `source_fidelity` 로그 전달.
- 미완료: 제품 수준 image-to-structure 추출, production-quality vectorization, raster-hybrid output. 이 작업이
  끝나기 전까지 image 입력은 partial feature로 문서화하고 테스트한다.

Generate 응답은 의도적으로 작다.

```json
{
  "request_id": "...",
  "candidates": [{ "id": "...", "png_url": "https://..." }],
  "warnings": []
}
```

응답은 SVG, intent, layout_id, source_fidelity, repro metadata를 반환하지 않는다. 이 값들은
`app/logs/generation_log.py`가 `seamless_generation_logs`에 서버 사이드로 저장한다. 로그 저장은
best-effort이며 `SUPABASE_DB_URL`이 없으면 no-op이다.

## Intent와 엔진 계약

권위 있는 intent schema는 `app/engine/intent.py`다. API request/response 모델은
`app/api/schemas/generate.py`다.

핵심 intent 개념:

- `canvas`: `tile_mm`, `dpi`.
- `palette` + `colorways`: layer는 color slot ID를 참조하고, 활성 colorway가 실제 출력 색을 해석한다.
- `layers`: `background`, `stripe`, `motif`; `(z_order, id)`로 안정 정렬한다.
- `placement`: motif layer만 instance 배치를 가진다. 지원 전략은 `lattice`, `point_set`,
  `path_following`, `scatter`.
- `motif_id`: engine 실행 전 모든 motif geometry는 concrete ID로 확정되어야 한다.

검증은 두 단계다.

- 구조 검증은 `extra="forbid"` pydantic 모델이 담당한다.
- 시맨틱 검증은 `app/validate/intent.py`가 담당한다. palette/colorway coverage, host/lane reference,
  placement compatibility, tile-commensurate stripe, wave closure, lattice/scatter constraints, motif color slot
  binding, raster limit, DPI/spacing/stripe period 같은 제한적 결정론 repair를 여기서 처리한다.

결정론 계약:

- 같은 `intent_version`, resolved intent, seed, colorway, engine version, registry pool fingerprint는
  byte-identical SVG를 만들어야 한다.
- randomness는 `random.Random(seed)`만 사용한다. global random, 현재 시각, dict insertion order는 설계 입력이
  아니다.
- stable ID는 `app/engine/determinism.py`의 canonical JSON/hash helper에서 만든다.
- adapter 산출물은 engine 경계로 들어오기 전에 cache/freeze한다.

## Seamless SVG 모델

seamless는 pixel repair가 아니라 by-construction으로 보장한다.

- angle과 stripe period는 tile-commensurate 값으로 snap/validate한다.
- path-following spacing은 tile edge가 아니라 lane closure length 기준으로 해석한다.
- lattice/drop/scatter 전략은 torus 위에서 동작해 edge continuity를 구조적으로 맞춘다.
- tile boundary를 넘는 motif instance에는 shifted clone `<use>`를 추가한다.
- composition은 `<pattern patternUnits="userSpaceOnUse">`, `<defs>`, `<symbol>`, `<use>` 기반의 단일 SVG
  문서를 만든다. primitive가 최종 SVG 문서를 직접 만들지 않는다.

`app/validate/seamless.py`의 raster seam metric은 회귀 가드다. anti-aliasing과 renderer 차이 때문에 pixel
diff는 1차 증명이 될 수 없다.

## Motif

Motif는 단순 mark부터 detailed multicolor shape까지 포함하는 재사용 SVG symbol이다.

```text
motif spec
  -> exact/facet/embedding lookup in motif store
  -> miss generates via Recraft
  -> sanitize + normalize_motif_svg + structural gates
  -> content-hash motif_id
  -> register in memory
  -> best-effort Postgres upsert
```

중요 계약:

- production built-in motif는 `MOTIFS`에 ship하지 않는다. 런타임 motif는 생성/등록된다.
- motif는 unit-box symbol, `bbox_mm`, `anchor`, `color_slots`로 정규화된다.
- single-color motif는 `color`로, multicolor motif는 모든 local slot을 `colors`로 바인딩한다.
- resolver reuse는 cache 성격이다: exact descriptor match, `scope` hard filter, embedding similarity, generate
  순서로 진행한다.
- variant 선택은 `variant_group`과 seed의 순수 함수다.

## Persistence와 소유권

이 레포는 DB schema를 소유하지 않는다.

- React 모노레포가 Supabase schema/migration을 소유한다. 이 레포에서 `supabase/migrations/`를 만들거나
  `supabase db push`, `supabase db reset`을 실행하지 않는다.
- `SUPABASE_DB_URL`은 `app/motifs/store.py`와 generation logging이 쓰는 서버 사이드 direct Postgres DSN이다.
  RLS를 우회하므로 client에 노출하면 안 된다.
- `motifs` row는 psycopg로 읽고 쓰며, content-hash `id`와 `ON CONFLICT DO NOTHING`으로 멱등성을 둔다.
- `seamless_generation_logs`는 request metadata, resolved intents, candidate SVGs, preview URLs, timing을
  보존한다.
- `SUPABASE_DB_URL`이 없으면 motif persistence와 generation logging은 no-op/in-memory로 degrade한다.
- Preview PNG upload는 `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`로 Supabase Storage REST를 사용한다. 설정이
  없으면 `png_url`은 `null`이고 warning을 반환한다.

## Public API

Routes는 `/api/v1` 아래에 mount된다.

- `GET /health`: health probe.
- `GET /palettes`: named palette presets.
- `POST /generate`: candidate ID와 preview PNG URL을 반환한다.
- `POST /export`: client SVG를 scrub한 뒤 PNG/TIFF로 rasterize해 binary data를 반환한다.

Error policy:

- Request schema validation: `400`.
- Intent semantic validation 또는 unsafe export SVG: route boundary에 따라 `422`/`400`.
- External adapter, renderer, store dependency failure: `502`.
- Valid request 이후 candidate를 하나도 합성하지 못함: `500`.
- 모든 error body는 `detail`, `request_id`를 포함하고, response에는 `X-Request-ID`가 붙는다.

## 보안 경계

- SVG parsing은 `defusedxml`을 사용한다. sanitize/scrub은 tag/attribute allowlist와 external href 거부를
  적용한다.
- Engine output SVG는 `sanitize_svg`를 통과해야 한다. 신뢰할 수 없는 export input은 rasterization 전에
  `scrub_svg`를 거친다.
- Image input은 로컬 decode만 한다. 원격 URL fetch가 없으므로 image upload 경로의 SSRF는 범위 밖이다.
- Reference image는 encoding/decoded size cap, allowed formats, pixel cap, integrity verification, metadata
  stripping을 거친 뒤 palette extraction에 들어간다.
- 외부 model/storage credential은 서버 사이드 env var로만 둔다.

## 핵심 결정

- Deterministic engine boundary: adapter는 비결정적일 수 있지만 freeze된 intent/motif ID만 engine에 들어간다.
- Vector-first output: SVG가 canonical이고 preview/export는 파생물이다.
- Schema ownership: Supabase DDL은 React 모노레포가 소유하고 이 레포는 client-only다.
- Slim generate response: public response는 preview URL만 담고, 재현 데이터는 server-side log에 둔다.
- Docs policy: `ARCHITECTURE.md`와 pydantic schema/test가 현재 truth다. 오래된 `docs/plan/*` 파일은 명시적으로
  갱신하지 않는 한 legacy implementation record다.

## 검증 앵커

아키텍처 동작을 정의하는 주요 테스트:

- `tests/test_api_generate.py`: generate response shape, caching, warnings, request ID, error mapping.
- `tests/test_adapters.py`: prompt/image adapter seam, cache freezing, upload hardening.
- `tests/test_candidates.py`, `tests/test_determinism.py`: candidate ranking, de-dup, repro determinism.
- `tests/test_motif_resolver.py`, `tests/test_motif_store.py`, `tests/test_registry_fingerprint.py`: motif reuse,
  persistence, registry pool fingerprint.
- `tests/test_seamless.py`, `tests/test_composition.py`, `tests/test_render_svg.py`, `tests/test_sanitize.py`:
  SVG topology, seamless guard, rendering/sanitization behavior.
