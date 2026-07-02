# 아키텍처 - Seamless Tile

`seamless-tile`은 텍스트 prompt 또는 내부 intent JSON을 입력받아 seamless textile tile의 SVG 후보를
생성하는 FastAPI 서비스다. one-shot 생성 위에 **대화형 편집 세션**(session_id 기반 turn, 비용 게이트,
영속·time-travel)과 **결정론적 원단 텍스처 finalize**, **motif 재사용 τ 보정 하니스**가 완결되어
있다. 현재 1차 개발 기준으로 `generate`의 `reference_image` 제품 경로만 미완료 영역이며, 나머지
prompt/intent 생성, 대화형 세션, motif 해석, SVG 합성, 원단 texture finalize, preview/log/export
경로는 현재 코드 기준으로 완료된 아키텍처로 본다.

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
        +-- Gemini: prompt -> intent JSON + motif specs (authoring)
        +-- Gemini (bind_tools): 편집 turn의 도구 호출 제안 (세션)
        +-- OpenAI embeddings: motif descriptor 유사도, τ-gated 재사용
        +-- Recraft: detailed/multicolor vector motif generation (비용 게이트 뒤)
        +-- Supabase Postgres: motifs, seamless_generation_logs, seamless_sessions(미러),
        |                       LangGraph checkpoints/checkpoint_blobs/checkpoint_writes
        +-- Supabase Storage: generated preview PNG + finalize 텍스처 PNG
        +-- rsvg-convert/resvg: SVG -> PNG/TIFF rasterization
        +-- Pillow: 결정론적 원단 텍스처 합성 (finalize, 외부 호출 0)
```

경계 규칙:

- LLM/VLM/generator는 최종 SVG tile을 그리지 않는다. intent JSON, motif spec, 재사용 motif SVG까지만 만든다.
- 결정론 엔진이 좌표, 배치, layer 합성, seamless 불변식, 최종 SVG bytes를 소유한다.
- **세션 그래프(LangGraph)는 authoring 계층 확장일 뿐이다.** candidate/placement/composition/seamless
  엔진 단계를 감싸거나 대체하지 않는다 — 세션 턴도 결국 concrete motif가 확정된 frozen intent를 같은
  결정론 엔진 함수로 넘긴다.
- **해석과 지출은 분리한다.** LLM/편집 도구는 제안·분류만 하고, 비싼 전이(Recraft 생성)는 결정론
  정책 또는 명시적 사용자 확인(interrupt→resume)으로만 게이트한다.
- Supabase schema/migration은 React 모노레포가 소유한다. 이 레포는 DB client일 뿐이며, motifs 테이블과
  LangGraph checkpoint 테이블 모두 이 규칙을 따른다.
- SVG가 원본이다. PNG/TIFF, preview URL, 원단 texture PNG는 모두 파생물이다.

## 런타임 컨테이너

```text
app/main.py
  FastAPI app, /api/v1 router mount, lifespan, request-id/error middleware

app/core/
  Settings(`config.py`: tau, 세션 budget 상한, DPI cap 등 env-driven tunable), observability(request-id,
  구조화 metrics)

app/api/
  Public HTTP schemas and routes: generate, sessions, finalize, export, palettes, health

app/adapters/
  Non-deterministic or external seams: LLM(authoring/edit), image, embeddings, Recraft, motif resolver

app/validate/
  Intent 시맨틱 검증(`intent.py`)과 seamless raster 가드(`seamless.py`) — Validation Engine의 구현체

app/engine/
  Deterministic intent -> SVG engine: candidate, placement, composition, seamless, determinism helper

app/sessions/
  LangGraph 세션 그래프(author/edit/gate/validate/commit), 편집 도구 화이트리스트, 세션 상태,
  checkpoint 백엔드(in-memory/Postgres client), 결정론 비용 가드(budget/in-flight lock)

app/motifs/
  In-memory motif registry, SVG intake normalization, geometry/facets, Postgres motif store

app/render/, app/storage/, app/logs/
  SVG sanitize/rasterization, 결정론 원단 텍스처 합성(fabric.py), Supabase preview/finalize 업로드,
  best-effort generation logging
```

Route layer는 얇게 유지한다. 오래가는 동작은 `app/engine/*`, 외부 불확실성은 `app/adapters/*`,
대화 상태·게이트·비용가드는 `app/sessions/*`, DB/storage/logging 실패 격리는 `app/motifs/*`,
`app/storage/*`, `app/logs/*`에 둔다.

## 내부 엔진 책임

이 서비스의 핵심은 외부 adapter(및 세션 authoring)가 만든 intent를 deterministic engine으로 닫는
구조다. 각 내부 엔진은 서로의 구현 세부에 기대지 않고 명확한 계약만 주고받는다. 엔진은 순수 Python
함수 호출로만 실행되며, LangGraph 노드도 세션 상태도 알지 못한다.

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
  해석해 `<pattern>`, `<defs>`, `<symbol>`, `<use>` 기반의 단일 SVG 문서로 합성한다. finalize의 region
  세그맵 렌더도 이 진입점을 재사용한다(신규 엔진 코드 0).
- **Seamless Engine** (`app/engine/seamless.py`): by-construction 불변식을 재단언하고, 경계 밖으로 나가는
  motif instance에 shifted clone을 추가해 tile edge 연속성을 유지한다.
- **Motif Registry/Resolver** (`app/motifs/*`, `app/adapters/motif_resolver.py`): prompt/편집이 만든 motif spec을
  재사용 가능한 concrete `motif_id`로 확정한다. 이 단계는 engine 전에 끝나며, engine은 항상 concrete motif만
  본다.
- **Render/Export/Finalize Boundary** (`app/render/*`, `app/api/routes/export.py`,
  `app/api/routes/finalize.py`): SVG를 sanitize/scrub하고 필요할 때만 PNG/TIFF로 rasterize하거나, 원단
  텍스처 PNG로 합성한다. raster/texture는 모두 파생물이며 engine determinism의 입력이 아니다.

## Generate 흐름

`POST /api/v1/generate`는 `intent`, `reference_image`, `images`, `prompt` 중 하나를 받는다. 우선순위는
`intent > images > reference_image > prompt`다(요청에 `reference_image`와 `images`를 동시에 실으면
`images` 경로가 선택된다 — 둘 사이 상호 배타 검증은 없다). optional `session_id`/`from_checkpoint`가
있으면 stateless 대신 세션 턴 경로로 들어간다(둘 다 없으면 기존 stateless 동작·응답이 그대로 불변).

현재 `reference_image` 상태:

- 구현됨: base64/data-URI 검증, 이미지 크기/포맷 hardening, metadata stripping, 결정론적 median-cut
  palette 추출, `source_fidelity="vector"` 고정 로그 전달. `app/adapters/image.py`는 의도적으로
  palette-only이며 motif inference(VLM)/vectorization은 시도하지 않는다(과거 있던 주입 seam은 제거됨).
- 미완료: 제품 수준 image-to-structure 추출, production-quality vectorization, raster-hybrid output. 이
  작업이 끝나기 전까지 image 입력은 partial feature로 문서화하고 테스트한다. (참고: 멀티 이미지 chat
  경로인 `images`/`source_image_index`는 Recraft 기반 vectorize를 별도로 쓰지만, 이 필드와는 다른
  경로다.)

```text
GenerateRequest
  -> request/cache key = request body + registry pool fingerprint (stateless 응답 캐시는 opt-in, 기본 off,
     process-local — 멀티 워커 배포 시 공유 캐시로 승급 필요)
  -> session_id 없음 (stateless):
       입력 해석:
         intent          -> 그대로 사용, prompt/image/canvas/palette는 무시 경고
         images          -> 멀티 이미지 chat 경로: LLM이 각 이미지에 style/motif role을 바인딩
         reference_image -> image adapter seam (현재 제품 경로 미완료)
         prompt          -> Gemini designs[] = intent + motif_specs
       motif_resolver가 engine 실행 전에 concrete motif_id를 자동 재사용/생성으로 주입
       validate_intent + assert_seamless_invariants
       generate_candidate_set이 layout, colorway, seed 축으로 후보 생성
       compose가 SVG candidates를 결정론적으로 합성
       Supabase Storage 설정 시 preview PNG render/upload
       slim response 반환: request_id, candidates[].{id,png_url}, warnings[]
       background log에 resolved intent, SVG, layout_id, source_fidelity, repro data 저장
  -> session_id 있음:
       LangGraph 세션 그래프가 한 턴을 실행한 뒤 stateless와 동일한 commit 경로로 합류한다
       (그래프 단계 전체는 "대화형 세션" 절 참고)
       응답 = stateless slim 응답 + session_id, turn_id, (대기 중이면) pending
```

Generate 응답은 의도적으로 작다.

```json
{
  "request_id": "...",
  "candidates": [{ "id": "...", "png_url": "https://..." }],
  "warnings": []
}
```

세션 턴 응답은 여기에 `session_id`, `turn_id`, (게이트 대기 중이면) `pending`이 추가된다 — 셋 다
null이면 직렬화에서 제거되므로 stateless 응답 byte shape은 변하지 않는다. 응답은 SVG, intent,
layout_id, source_fidelity, repro metadata를 반환하지 않는다. 이 값들은 `app/logs/generation_log.py`가
`seamless_generation_logs`에 서버 사이드로 저장한다. 로그 저장은 best-effort이며 `SUPABASE_DB_URL`이
없으면 no-op이다.

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
  byte-identical SVG를 만들어야 한다. registry pool fingerprint는 현재 reusable motif pool의 정렬된
  motif ID 목록에 대한 해시다(`registry_version`에 `+pool.<hex8>`로 스탬프) — motif를 새로 생성/등록하면
  이 값이 바뀌어 캐시/세션 비교가 stale pool을 가리키지 않게 한다.
- randomness는 `random.Random(seed)`만 사용한다. global random, 현재 시각, dict insertion order는 설계 입력이
  아니다.
- stable ID는 `app/engine/determinism.py`의 canonical JSON/hash helper에서 만든다.
- adapter 산출물은 engine 경계로 들어오기 전에 cache/freeze한다.
- 세션의 `apply_tools`도 이 계약 안에 있다: 같은 `(이전 intent, 도구 호출 열)`은 현재 시각·무작위·dict
  순서에 기대지 않고 같은 새 intent를 만든다.

## Seamless SVG 모델

seamless는 pixel repair가 아니라 by-construction으로 보장한다.

- angle과 stripe period는 tile-commensurate 값으로 snap/validate한다.
- path-following spacing은 tile edge가 아니라 lane closure length 기준으로 해석한다.
- lattice/drop/scatter 전략은 torus 위에서 동작해 edge continuity를 구조적으로 맞춘다.
- tile boundary를 넘는 motif instance에는 shifted clone `<use>`를 추가한다.
- composition은 `<pattern patternUnits="userSpaceOnUse">`, `<defs>`, `<symbol>`, `<use>` 기반의 단일 SVG
  문서를 만든다. primitive가 최종 SVG 문서를 직접 만들지 않는다.

`app/validate/seamless.py`의 raster seam metric은 회귀 가드다. anti-aliasing과 renderer 차이 때문에 pixel
diff는 1차 증명이 될 수 없다. finalize의 원단 텍스처 합성도 이 가드를 그대로 통과해야 한다(아래
"Finalize" 절 참고) — blur 기반 이음매 보정은 쓰지 않는다.

## Motif

Motif는 단순 mark부터 detailed multicolor shape까지 포함하는 재사용 SVG symbol이다.

```text
motif spec
  -> exact/facet/embedding lookup in motif store
  -> miss generates via Recraft (stateless: 자동 / 세션: 비용 게이트 뒤 명시적 확인)
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
  순서로 진행한다. embedding **미설정**(키 없음)은 graceful(soft-similarity 생략)이지만, embedding **호출
  실패**(OpenAI 다운)는 임의 motif 재사용 대신 `502`로 끝낸다.
- variant 선택은 `variant_group`과 seed의 순수 함수다.
- reuse/generate 컷오프인 `motif_similarity_tau`(기본 **0.84**, `app/core/config.py`)는 더 이상 추측값이
  아니다. `scripts/eval_motif_retrieval.py`가 `tests/fixtures/motif_eval/labelset.json`(exact/
  paraphrase/near-miss/off-corpus/cross-scope 쿼리)로 τ를 스윕해 precision/recall/generate-rate/
  false-reuse-rate 곡선을 만들고, **zero-false-reuse를 만족하는 최소 τ**를 근거값으로 고른다. 이
  하니스는 순수 오프라인 스크립트/픽스처이며 런타임 결정론·엔진 경계를 바꾸지 않는다.
- stateless 경로는 miss 시 자동으로 Recraft를 호출한다. 세션 경로는 아래 "대화형 세션"의 비용 게이트를
  통과해야만 호출한다 — 같은 resolver, 다른 트리거 정책.

## 대화형 세션 (Sessions)

`session_id`를 붙이면 `POST /api/v1/generate`가 stateless 생성 대신 **편집 턴**이 된다. (엔진 경계는
"시스템 컨텍스트"의 경계 규칙과 동일하다 — 세션 턴도 concrete motif가 확정된 frozen intent를
stateless와 동일한 엔진 함수로 넘긴다.)

```text
START
  -> 조건부 분기: current_intent 없음 -> author_intent / 있음 -> edit_intent
  -> author_intent: build_intents()[0] 재사용. 즉시 해석 가능한 motif_specs(텍스트/이미지 지정)는
     바로 resolve, 나머지는 게이트로 미룬다.
  -> edit_intent: edit LLM(Gemini bind_tools)이 화이트리스트 도구 호출을 제안 -> apply_tools가
     결정론 Python으로 적용.
  -> resolve_gate: 대기 중인 motif_spec마다 무료 재사용 후보를 제시하고 interrupt로 턴을 일시정지
     (=checkpoint). 대기 중인 spec이 남아있는 동안 반복한다.
  -> validate: 시맨틱 검증. 실패하면 편집 턴에 한해 1회만 (직전 오류를 지시문에 덧붙여) 자동 재시도.
  -> commit: generate_candidate_set + compose(stateless와 동일 경로)를 실행하고 checkpoint에 커밋,
     seamless_sessions에 best-effort 미러.
  -> END
```

- **세션 상태**(`app/sessions/state.py`): `session_id`, `turns`, `current_intent`, `current_candidates`,
  `seed`, `colorway`, `registry_version`, `material_map`, `budget`가 턴 사이에 보존되고, 게이트 진행 중엔
  `working_intent`/`pending_specs`/`pending_candidates`, 검증 재시도용 `validate_errors`/`edit_retried`가
  쓰인다.
- **편집 도구 화이트리스트**(`app/sessions/tools.py`): `set_colorway`, `set_palette_slot`, `scale_motif`,
  `set_stripe`, `set_density`, `add_layer`, `remove_layer`, `swap_motif`, `set_seed`, `regenerate`,
  `set_material` — 전부 `Intent`에 대한 순수 결정론 patch(`model_copy`)이고 **어댑터를 호출하지 않는다**.
  LLM은 도구 선택·인자 제안만 하고, 인자 검증과 실제 적용은 항상 Python이 한다. `set_colorway`/
  `set_seed`/`set_material`은 엔진 intent가 아니라 세션 상태만 바꾼다(`set_material`은 아래 Finalize가
  소비하는 material map 항목). 화이트리스트 밖 도구/인자 오류는 조용히 skip되고 경고만 남는다 —
  `validate_intent`가 구조적 backstop.
- **비용 게이트**: `resolve_gate`가 exact/scope/embedding 캐스케이드로 만든 무료 재사용 후보를
  `interrupt(...)`로 제시한다(LangGraph human-in-the-loop). 사용자가 `select-motif`로 기존 후보를
  고르면 무료 즉시 커밋되고, `confirm{action:"generate_motif"}`으로 명시적으로 확인해야만 Recraft가
  호출된다. **해석(제안)과 지출(Recraft 호출)은 항상 분리**되어 있다.
- **엔드포인트**(`app/api/routes/sessions.py`, prefix `/sessions`): 상세는 "Public API" 절 참고.

## 세션 영속과 time-travel

- **checkpoint 백엔드**(`app/sessions/checkpointer.py`): `SUPABASE_DB_URL` 미설정 시 LangGraph
  `MemorySaver`(in-memory, 세션 16과 동형). 설정 시 `psycopg_pool` 커넥션 풀 위의 `PostgresSaver` —
  **`setup()`은 절대 호출하지 않는다.** 부팅 시 read-only probe로 체크포인트 테이블 존재와 마이그레이션
  버전만 확인한다(스키마 소유권·테이블 부재/버전 불일치 시의 clean-fail 정책은 "Persistence와 소유권"
  절 참고).
- **`seamless_sessions`**(`app/sessions/store.py`): 매 턴 커밋/파이널라이즈 시 세션 요약(상태, seed,
  colorway, registry_version, current_intent)을 upsert하는 **best-effort UI 미러**다. 복원의 진짜
  출처는 항상 checkpointer이고, 이 테이블은 읽기 경로가 없다 — 실패해도 warning만 남기고 턴은 계속된다.
- **결정론 비용 가드**(`app/sessions/budget.py`): 세션당 Recraft/finalize 호출 횟수 상한
  (`session_recraft_limit`/`session_finalize_limit`, 기본 3/10)을 순수 카운터로 추적하고, 초과 시
  확인 이벤트에도 429로 거부한다(resource-ceiling 정신). 같은 세션에 대한 동시 mutating 요청은
  프로세스-로컬 in-flight lock으로 직렬화되어 409를 반환한다(멀티 워커 배포 시 공유 lock으로 승급
  필요 — 코드에 ponytail 주석으로 표시됨).
- **undo/redo/fork**: 각 턴 경계가 체크포인트다. `GET /sessions/{id}?checkpoint_id=`로 과거 턴을 읽기
  전용 복원하고(undo/redo — `update_state`를 호출하지 않아 head는 움직이지 않는다),
  `POST /generate`에 `from_checkpoint`를 주면 그 지점에서 새 편집 턴을 실행해 LangGraph의 checkpoint
  기반 fork로 **원래 분기를 건드리지 않고** 새 분기를 만든다. 존재하지 않거나 다른 세션의
  `from_checkpoint`(해당 checkpoint에 `current_intent`가 없음)는 턴을 만들기 전에 **404**로 막는다 —
  head 이동도, 새 checkpoint 생성도 없다. 복원/fork된 지점의 재합성은 원본과 byte 동일함이 테스트로
  봉인되어 있다. fork 시 budget은 fork 지점이 아니라 스레드 head 기준으로 이어받아, 되감기로 지출을
  환불받을 수 없다.

## Finalize — 결정론적 원단 텍스처 렌더

승인된 seamless 후보를 "천 느낌" PNG로 만드는 별도의 결정론 파생 경로다(`app/render/fabric.py`).
생성형 모델·외부 API를 전혀 쓰지 않는다.

```text
FinalizeRequest{intent, colorway_id?, production_method?, weave, material_map?, dpi?,
                texture_strength?, relief_strength?}
  -> compose(intent, colorway) 재실행 -> rasterize (stateless generate와 동일한 결정론 합성 재사용;
     저장된 SVG를 다시 읽지 않는다 — intent+colorway만 있으면 byte 동일 재현 가능하고 region
     텍스처링에도 intent가 필요하기 때문)
  -> 번들 tileable weave PNG를 wrap 샘플링으로 디자인 픽셀 크기에 정확히 맞춰 이음매 없이 타일링
  -> multiply 합성 + texture_strength로 질감(어두워짐) 강도 조절
  -> production_method="yarn_dyed"인 경우:
       label colorway(slot마다 구분되는 hue)로 만든 region 세그맵으로 slot별 다른 weave를
       마스크 합성 (겹치지 않는 마스크라 합성 순서/dict 순서 무관 -> 결정론 유지)
       모티프 영역은 대각선 yarn thread inlay로 별도 렌더(고정 twill-45 -- base weave/material_map의
       영향을 받지 않음) + relief_strength로 색 경계를 엠보싱(blur 없는 wrap 오프셋)
  -> material_map이 비어있으면 균일 weave 결과와 byte 동일 (폴백, 테스트로 봉인)
```

- **weave 자산**(`app/render/assets/fabric/*.png`, `available_weaves()`로 동적 열거): `check`,
  `herringbone`, `jacquard`, `pindot`, `solid`, `twill-0`, `twill-45` — 버전 pin된 결정론 입력의 일부.
- **`POST /api/v1/finalize`**: 렌더는 executor(별 스레드)에서 돌고, 결과 PNG는 content-hash 경로
  (`fabric/<sha256[:16]>.png`)로 preview와 같은 Storage 버킷에 업로드된다. Storage 미설정/실패 시
  `image_url` null + warning(하드 실패 아님). `IntentInvalid`는 422, `FabricError`는 400으로 매핑된다.
- **결정론/seamless**: 같은 `(intent, colorway, weave, material_map, dpi, texture/relief 강도, 자산
  버전, Pillow 버전)`은 byte 동일 PNG를 만든다. 이음매는 blur 없이 wrap 오프셋으로만 만들어
  `validate/seamless.py`의 raster seam 가드를 texture grain 감안 tolerance로 통과한다.
- **세션 연계**: 세션의 `set_material` 도구는 세션 상태의 rich material map
  (`{slot: {fabric, finish, lighting}}`)에만 기록되고, 엔진 intent는 건드리지 않는다.
  `POST /sessions/{id}/confirm{action:"finalize"}`가 이를 `{slot: weave}`로 변환해(palette slot이고
  `available_weaves()`에 있는 fabric만 채택; finish/lighting/레이어 타깃은 래스터 표현 없어 무시)
  요청의 `material_map`과 슬롯 단위로 병합한다 — **같은 slot이면 요청이 이긴다.**

## Public API

Routes는 `/api/v1` 아래에 mount된다.

- `GET /health`: health probe.
- `GET /palettes`: named palette presets.
- `POST /generate`: candidate ID와 preview PNG URL을 반환한다. optional `session_id`/`from_checkpoint`로
  대화형 세션 턴/fork 경로에 들어간다.
- `POST /export`: client SVG를 scrub한 뒤 PNG/TIFF로 rasterize해 binary data를 반환한다.
- `POST /finalize`: 승인 후보의 intent를 원단 텍스처 PNG로 렌더한다("Finalize" 절 참고).
- `GET /sessions/{id}?checkpoint_id=`: 대화 이력·커밋된 후보·게이트 상태를 복원한다(`checkpoint_id`
  주면 과거 턴의 읽기 전용 undo/redo 스냅샷).
- `GET /sessions/{id}/checkpoints`: undo/redo/fork용 턴 경계 체크포인트 목록.
- `POST /sessions/{id}/select-motif`: 게이트에 제시된 기존(무료) 모티프 후보를 확정.
- `POST /sessions/{id}/confirm`: `action="generate_motif"`(Recraft 생성 승인, 비용 게이트 통과) 또는
  `action="finalize"`(커밋된 후보를 원단 텍스처로 렌더).

Error policy:

- Request schema validation: `400`.
- Intent semantic validation 또는 unsafe export SVG: route boundary에 따라 `422`/`400`.
- External adapter, renderer, store dependency failure(checkpoint 백엔드 포함): `502`.
- Valid request 이후 candidate를 하나도 합성하지 못함: `500`.
- 알 수 없거나 타 세션의 `from_checkpoint`, 존재하지 않는 session/motif/candidate: `404`.
- 게이트 대기 중 충돌하는 동작, 같은 세션에 대한 동시 mutating 요청(in-flight lock): `409`.
- 세션 비용 상한(Recraft/finalize) 초과: `429`.
- 모든 error body는 `detail`, `request_id`를 포함하고, response에는 `X-Request-ID`가 붙는다.

## 보안 경계

- SVG parsing은 `defusedxml`을 사용한다. sanitize/scrub은 tag/attribute allowlist와 external href 거부를
  적용한다.
- Engine output SVG는 `sanitize_svg`를 통과해야 한다. 신뢰할 수 없는 export input은 rasterization 전에
  `scrub_svg`를 거친다.
- Image input은 로컬 decode만 한다. 원격 URL fetch가 없으므로 image upload 경로의 SSRF는 범위 밖이다.
- Reference image는 encoding/decoded size cap, allowed formats, pixel cap, integrity verification, metadata
  stripping을 거친 뒤 palette extraction에 들어간다.
- 외부 model/storage credential은 서버 사이드 env var로만 둔다. `SUPABASE_DB_URL`은 RLS를 우회하는
  서버 사이드 전용 direct Postgres DSN이라 client에 노출하면 안 된다(어떤 컴포넌트가 공유하는지는
  "Persistence와 소유권" 절 참고).

## Persistence와 소유권

이 레포는 DB schema를 소유하지 않는다. motif 테이블과 세션/checkpoint 테이블 모두 동일한 규칙을
따른다.

- React 모노레포가 Supabase schema/migration을 소유한다. 이 레포에서 `supabase/migrations/`를 만들거나
  `supabase db push`, `supabase db reset`, LangGraph checkpointer의 `.setup()`을 실행하지 않는다.
- `SUPABASE_DB_URL`은 `app/motifs/store.py`, generation logging, `app/sessions/store.py`/
  `app/sessions/checkpointer.py`가 공유하는 서버 사이드 direct Postgres DSN이다.
- `motifs` row는 psycopg로 읽고 쓰며, content-hash `id`와 `ON CONFLICT DO NOTHING`으로 멱등성을 둔다.
- `seamless_generation_logs`는 request metadata, resolved intents, candidate SVGs, preview URLs, timing을
  보존한다.
- LangGraph checkpoint 4테이블(`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
  `checkpoint_migrations`)과 `seamless_sessions`(앱 정의, best-effort 세션 미러)는 모노레포가
  `langgraph-checkpoint-postgres`의 핀된 버전에 맞춰 선정의한다. 이 레포는 read-only probe로 존재/버전만
  확인하고, 테이블이 없거나 버전이 안 맞으면 self-provision 대신 명확한 client 에러(502)로 끝낸다.
- `SUPABASE_DB_URL`이 없으면 motif persistence, generation logging, 세션 checkpoint 모두 no-op/
  in-memory로 degrade한다.
- Preview PNG/finalize 텍스처 PNG 업로드는 `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`로 Supabase Storage
  REST를 사용한다. 설정이 없으면 URL은 `null`이고 warning을 반환한다.

## 핵심 결정

- Deterministic engine boundary("경계 규칙" 참고): adapter/세션은 비결정적일 수 있지만 freeze된
  intent/motif ID만 engine에 들어간다.
- 해석과 지출 분리("경계 규칙" 참고): LLM은 제안만 하고, Recraft 생성 같은 비싼 전이는 명시적 확인 뒤에만
  나간다.
- Vector-first output("경계 규칙" 참고): SVG가 canonical이고 나머지는 전부 파생물이다.
- Schema ownership("Persistence와 소유권" 참고): Supabase DDL은 React 모노레포가 소유하고 이 레포는
  client-only다. 테이블 부재/버전 불일치는 self-provision이 아니라 clean-fail이다.
- Slim generate response: public response는 preview URL과 (세션이면) session_id/turn_id/pending만 담고,
  재현 데이터는 server-side log에 둔다.
- 원단 finalize는 생성형 모델 0개, 결정론 Pillow 합성이다 — 원단 자산은 버전 pin된 결정론 입력이다.
- motif reuse τ는 추측값이 아니라 라벨셋 기반 근거값이다(현재 0.84, zero-false-reuse 최소 τ).
- Docs policy: 이 문서와 pydantic schema/test가 현재 truth이고, `docs/plan/*`은 명시적으로 갱신하지 않는
  한 legacy record다(자세한 내용은 문서 서두 참고).

## 검증 앵커

아키텍처 동작을 정의하는 주요 테스트:

- `tests/test_api_generate.py`: generate response shape, caching, warnings, request ID, error mapping.
- `tests/test_adapters.py`: prompt/image adapter seam, cache freezing, upload hardening.
- `tests/test_candidates.py`, `tests/test_determinism.py`: candidate ranking, de-dup, repro determinism.
- `tests/test_motif_resolver.py`, `tests/test_motif_store.py`, `tests/test_registry_fingerprint.py`: motif reuse,
  persistence, registry pool fingerprint.
- `tests/test_seamless.py`, `tests/test_composition.py`, `tests/test_render_svg.py`, `tests/test_sanitize.py`:
  SVG topology, seamless guard, rendering/sanitization behavior.
- `tests/test_sessions.py`: 편집 국소성, 도구 화이트리스트 enforcement, 비용 게이트(무료 제시/유료
  confirm), apply_tools/세션 결정론, stateless 호환.
- `tests/test_session_persistence.py`: checkpoint 백엔드 선택, no-DDL/no-setup() 회귀 가드, 예산 가드,
  in-flight dedup, degrade(SUPABASE_DB_URL 없음, 테이블 부재 clean-fail), byte-identical 복원.
- `tests/test_time_travel.py`: undo(rewind) byte-identical 재합성, fork가 원 분기를 보존, redo.
- `tests/test_fabric.py`: 원단 텍스처 결정론, seamless 유지, region material map/폴백, motif thread
  inlay, 무외부호출.
- `tests/test_retrieval_eval.py`: 기본 τ의 precision/recall/false-reuse-rate 베이스라인, cross-scope
  hard filter.
