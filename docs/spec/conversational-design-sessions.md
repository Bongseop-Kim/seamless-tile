# Spec — 대화형 반복 디자인 세션 & 편집 도구 (LangGraph / LangChain / LangSmith)

`seamless-tile`을 **one-shot 생성기**에서 **대화형 디자인 스튜디오**로 확장하기 위한 설계 명세다.
현재 `/generate`는 완전 stateless(요청마다 prompt→intent→SVG 독립)라, "채팅으로 이미지를 만드는"
제품 정체성과 달리 **대화·세션·편집 이력**이 없다. 본 문서는 그 갭을 메우는 상위 기능층을 정의한다.

- 설계 기준: [ARCHITECTURE.md](../../ARCHITECTURE.md)(엔진 경계·결정론 계약), 관련 spec:
  [motif-library-and-multicolor.md](motif-library-and-multicolor.md),
  [photoreal-fabric-render.md](photoreal-fabric-render.md)(실사화 finalize 단계).
- 관련 코드: `app/api/routes/generate.py`(입력 경로·slim response), `app/adapters/llm.py`(prompt→intent),
  `app/adapters/motif_resolver.py`(motif 확정·재사용/생성 라우팅), `app/engine/intent.py`(Intent 스키마),
  `app/validate/intent.py`(시맨틱 검증·repair), `app/logs/generation_log.py`(best-effort 영속화 패턴),
  `app/core/observability.py`(request-id·metrics).
- 본 문서는 *설계 기준점*이며, 구현은 이후 세션 단위로 분해한다(§15).

---

## 0. 관통 원칙

1. **엔진은 성역, 확장은 어댑터에서.** 모든 확장은 authoring 계층(prompt→intent)에만 얹는다. 결정론
   엔진(intent→SVG)은 손대지 않는다. 세션 = **frozen intent들의 시퀀스**이고, 각 턴이 커밋한 intent는
   `(intent, seed, colorway, registry_version) → byte-identical SVG` 계약으로 재현 가능하다.
2. **해석 ≠ 지출.** LLM은 "무엇을 원하는가"를 *해석·제안*만 한다. **돈이 드는 이산 작업**(Recraft 모티프
   생성, 실사화)의 발동은 LLM 재량이 아니라 **① 결정론 정책** 또는 **② 명시적 사용자 확인/선택**으로만
   게이트한다(§8). 이 둘이 아래 전부의 안전장치다.
3. **위반 금지**: 엔진 단계(candidate/placement/composition/seamless)를 LangGraph 노드나 LLM 판단으로
   감싸지 않는다. 엔진은 언제나 concrete-motif가 확정된 frozen intent만 받는 순수 Python이다.

---

## 1. 배경 & 문제

- `/generate`는 **stateless**. 입력 경로는 `intent > reference_image > images > prompt`이고 요청 간
  공유 상태가 없다(`app/api/routes/generate.py`). 응답은 slim(request_id, candidates[].{id,png_url},
  warnings)이라 클라이언트가 직전 결과의 intent를 다시 들고 오지도 못한다(intent는 server-side 로그에만).
- LLM 표면은 단일 호출 `LLMClient.complete(prompt, images?) -> str`(+검증 실패 시 1회 재프롬프트).
  tool-use·멀티턴·상태 없음(`app/adapters/llm.py:32`).
- prompt 경로는 매번 intent JSON을 **처음부터 자유 작성**한다(`_build_prompt`).

해결할 문제:

1. **세션/대화 부재**: 편집 턴("방금 그거 유지하고 stripe만 45도")이 불가.
2. **편집 = 전체 재작성 drift**: 미세 수정을 흉내내려 프롬프트를 다시 던지면 LLM이 intent 전체를 새로 써
   의도치 않은 부분까지 바뀐다(비재현·불안정).
3. **비싼 작업의 통제 부재**: 이 서비스에서 비싼 건 **Recraft 모티프 생성**과 **실사화(Gemini repaint)**다.
   이걸 LLM 프롬프트 해석 하나로 자동 발동하면 오분류가 곧 비용·되돌리기 힘든 지출이 된다.
4. **관측·비용 가시성 부재**: 세션이 여러 LLM/도구 호출로 늘면 재생·비용 추적이 필요.

---

## 2. 범위 / 비범위

**범위(이 spec)**

- 세션 상태 모델 + 턴 처리(신규 authoring vs 편집).
- **편집 = intent delta**(전체 재작성 아님), LLM이 **화이트리스트 편집 도구**를 tool-use로 호출.
- **비용 게이트 & 확인 상태머신**(§8): 모티프 후보 선택, 모티프 재생성 승인, 실사화 버튼 트리거, 비용 가드.
- 세션 영속화(graceful degradation 패턴 준수) 및 복원 API.
- 결정론 계약의 세션 확장(§9) + 회귀 테스트.
- (선택) LangSmith 세션 트레이싱 — standalone·opt-in.

**비범위**

- planner/critic **멀티에이전트** — ROI 검증 후 별도(§15 후보 단계).
- **motif resolution의 agentic 검색 루프화** — 현재 deterministic semantic cache 유지.
- 엔진 내부 변경, `reference_image` 경로 고도화.
- 실사화 자체 구현 상세는 [photoreal-fabric-render.md](photoreal-fabric-render.md). 여기선 **트리거/게이트만** 다룬다.

---

## 3. 결정 (Decisions)

`✅` 확정 제안 · `❓` 착수 전 확인(§16).

| # | 결정 | 상태 | 비고 |
|---|---|---|---|
| S1 | 세션은 **frozen intent들의 시퀀스**. 각 턴 커밋 intent는 재현 가능 | ✅ | 결정론 무손실 근거 |
| S2 | 편집 턴은 **intent delta 적용**(전체 재작성 금지). LLM은 화이트리스트 **도구**만 호출 | ✅ | drift 제거 + LLM 제약 강화 |
| S3 | 도구 적용은 **결정론 Python**. 새 intent는 `validate_intent`(repair) 통과 | ✅ | `app/validate/intent.py` 재사용 |
| S4 | **LangGraph**로 세션 상태·턴 그래프·checkpointer. 엔진 단계는 순수 Python 노드 | ✅ | 분기/undo/resume first-class |
| S5 | tool-use는 **LangChain tool binding 또는 native google-genai function calling** 실측 선택 | ❓ | LangGraph가 orchestration 주체 |
| S6 | **LangSmith standalone·opt-in**. egress 승인 전제 | ❓ | SaaS egress + motif IP |
| S7 | 세션 영속: `SUPABASE_DB_URL` 있으면 저장, 없으면 in-memory degrade | ✅ | `generation_log` no-op 동형 |
| S8 | API: `POST /generate`에 optional `session_id`(있으면 `prompt`=편집 지시) + `GET /sessions/{id}` | ✅ | 기존 경로 재사용 |
| S9 | 분기/undo/redo/fork는 LangGraph checkpoint로 노출 | ✅ | **확정 제품 기능**(S14). 순서상 P0 세션 이후(P2) |
| S10 | motif 변경 편집만 `resolve_motifs` 호출. 팔레트/stripe/scale 등은 순수 intent patch | ✅ | 대부분 편집은 외부 호출 0 |
| **S11** | **비싼 이산 작업(Recraft 모티프 생성)은 명시적 확인/선택 이벤트로만 발동.** LLM은 제안·분류만, 자동 실행 금지. 실사화는 결정론·무료라 게이트 아님(§8.4) | ✅ | §0-2, §8 |
| **S12** | **모티프 해석 = 후보 제시 → 사용자 선택** 2단계. 재사용 후보(exact+embedding, 무료)를 top-K 제시. Recraft 생성은 사용자가 "새로 생성" 선택/승인할 때만 | ✅ | 인터랙티브 경로에서 generate-on-miss 자동(D5·D14)을 gate로 대체 |
| **S13** | **결정론 비용 가드**(LLM 무관): 세션당 Recraft 예산·레이트리밋, in-flight lock(중복 생성 방지), dedup, 비용/시간 힌트 | ✅ | resource-ceiling 정신 |
| **S14** | **LangGraph 채택 확정.** fork/undo(되감기·가지치기)를 **제품 기능으로 확정** → checkpointer/interrupt/time-travel을 프레임워크로 받는다. checkpoint 테이블 DDL은 **모노레포가 정의**(이 레포는 client로 붙기만) | ✅ | 사용자 결정. buy-vs-build 종결 |

---

## 4. 목표 아키텍처 (한 장)

```text
[신규] prompt / images ──┐                 [편집] session_id + 편집 지시(자연어)
                         │                                │
                         ▼                                ▼
   ┌──────────────── LangGraph 세션 그래프 (thread = session_id) ────────────────┐
   │  classify_turn ─▶ (신규)  author_intent : 기존 llm.build_intents             │
   │                └▶ (편집)  edit_intent   : 현재 intent + 지시 → 도구 호출     │
   │                            └▶ apply_tools : 결정론 Python 패치               │
   │                                                                              │
   │   ── motif 변경 시 ──▶ resolve_motifs (재사용 후보 top-K, 무료)              │
   │        └▶ [게이트 S12] 사용자 선택 ─ 기존 선택→무료  / "새로 생성"→[확인 S11]│
   │             └▶ Recraft 생성 (비쌈) → normalize → 후보 합류 → 재선택          │
   │                                                                              │
   │   validate_intent(repair)  ── checkpoint(턴) : undo/redo/fork ──             │
   └───────────────────────── frozen resolved intent ────────────────────────────┘
                                    │  ← 비결정·지출 끝. 아래는 성역.
                                    ▼
   generate_candidate_set → compose → seamless SVG 후보 N개  (기존 엔진, 무변경)
                                    │
        slim response + 세션 state 갱신 + best-effort 영속화
                                    │
        [UX 결정] 후보 확정 ─ 버튼 ─▶ photoreal-fabric-render (결정론 텍스처 렌더, 무료)
```

**불변식**: `frozen resolved intent` 아래는 기존 엔진 그대로. 비싼 전이(Recraft·실사화)는 **확인 게이트** 뒤에만.

---

## 5. 세션 상태 모델

```text
SessionState = {
  session_id: str,
  turns: list[Turn],                 # 대화 이력 (역할, 텍스트, 도구호출 요약, 게이트 이벤트)
  current_intent: dict | None,       # 마지막 커밋된 frozen resolved intent (재현 앵커)
  current_candidates: list[{id, png_url, svg_ref, ...}],  # 편집 컨텍스트/복원/실사화 대상
  pending: {motif_candidates?, awaiting_confirm?} | None,  # 게이트 대기 상태 (§8)
  seed: int, colorway: str | None,
  registry_version: str,             # pool fingerprint seal (기존)
  budget: {recraft_used, repaint_used, ...},  # 비용 가드 카운터 (S13)
}
```

- `current_intent`가 재현 앵커. 편집은 이 dict의 delta.
- 체크포인트 = 턴 경계. `(thread_id=session_id, checkpoint_id)`로 되감기·fork 노출(S9).
- 편집 컨텍스트로 LLM에 넘기는 것은 전체 intent가 아니라 **compact summary**(layers/palette/colorways/
  placement 요약) — 도구 인자는 layer_id/slot_id 등 안정 키로 참조.

---

## 6. 턴 처리

### 6.1 classify_turn
- `session_id` 없음 → **신규**. 있고 `current_intent` 존재 → **편집**. 명시적 "처음부터 다시"는 신규로 라우팅.

### 6.2 신규(author) 경로
- 기존 `llm.build_intents()` 재사용. 산출 intent를 `current_intent`로 커밋. (motif 획득은 §8.3 게이트 경유.)

### 6.3 편집(edit) 경로 — S2
- 입력: `current_intent` summary + 사용자 편집 지시.
- LLM은 §7 화이트리스트 도구를 **tool-use로 호출**(자유 JSON 재작성 금지).
- `apply_tools`가 각 도구를 **결정론 Python으로** 적용 → 새 intent 후보.
- motif를 바꾼 도구가 있으면 §8.3 모티프 게이트로 진입(즉시 Recraft 금지), 아니면 순수 patch.
- `validate_intent(new_intent, repair=True)` 통과 → 커밋. 실패 시 도구 인자 오류를 LLM에 피드백해 1회 재시도.

---

## 7. 편집 도구 API (tool-use 화이트리스트 — S2)

LLM이 호출 가능한 **닫힌 집합**. 각 도구는 intent 스키마(`app/engine/intent.py`)의 안전한 변형만 표현하고,
Python이 인자를 검증한 뒤 적용한다. 화이트리스트 밖 필드는 LLM이 건드릴 수 없다.

| 도구 | 인자 | 매핑 | 지출 게이트 |
|---|---|---|---|
| `set_colorway` | colorway_id | 활성 colorway 전환 | 없음(무료) |
| `set_palette_slot` | slot_id, hex | palette 슬롯 색 변경 | 없음 |
| `scale_motif` | layer_id, factor | motif `size_mm` 스케일 | 없음 |
| `set_stripe` | layer_id, angle?, period_mm? | stripe 파라미터 | 없음 |
| `set_density` | layer_id, spacing_mm/count | placement 밀도 | 없음 |
| `add_layer` / `remove_layer` | layer spec / layer_id | 레이어 추가·제거 | add가 motif면 §8.3 게이트 |
| `swap_motif` | layer_id, description, **prefer_reuse=true / force_new=false** | motif 교체 요청 → **후보 제시**(§8.3) | 재사용=무료, `force_new`/생성=**확인 필요(S11)** |
| `set_seed` | seed | variant 재추첨(결정론) | 없음 |
| `regenerate` | — | 같은 intent로 후보 재합성 | 없음(엔진 로컬) |
| `set_material` | target(layer_id/slot_id), fabric/finish/lighting | 영역별 원단 질감 지정(실사화 단계 소비) | 없음(무료·로컬) |

- **`swap_motif`는 스스로 Recraft를 호출하지 않는다.** resolver에게 후보 제시를 요청할 뿐이고, 실제 생성은
  §8.3의 사용자 선택/승인이 게이트한다(S11·S12).
- 인자 검증은 `validate/intent.py`의 기존 시맨틱 규칙(슬롯 참조·divisibility·commensurability) 재사용.
- 대부분 도구는 **어댑터 호출 0**(순수 intent patch, S10).
- **`set_material`은 엔진 intent를 바꾸지 않는다**: material map(배경/스트라이프/모티프별 질감)은 **실사화
  finalize 단계에서만 소비**되는 세션/finalize 상태다. 엔진은 material-agnostic → 결정론 경계 유지. 상세는
  [photoreal-fabric-render.md](photoreal-fabric-render.md) §5.6(영역별 텍스처링).

---

## 8. 비용 게이트 & 확인 상태머신 (S11–S13)

이 서비스에서 진짜 비싼 건 **Recraft 모티프 생성** 하나다. 실사화는 **결정론 텍스처 렌더(무료·로컬,
[photoreal-fabric-render.md](photoreal-fabric-render.md))**라 비싸지 않다(생성형 모델 미사용). 나머지
(prompt→intent, 모티프 재사용, 엔진, 프리뷰/텍스처 rasterize)도 싸거나 결정론·로컬이다. **비싼 것(Recraft)만**
LLM 자동 발동에서 떼어낸다.

### 8.1 원칙 (해석 ≠ 지출)
LLM은 **제안·분류**만. 비싼 이산 전이는 **결정론 정책** 또는 **명시적 사용자 이벤트**로만 발동(S11).

### 8.2 확인 상태머신
```text
designing ──(싼 루프: 편집·모티프 재사용·결정론 프리뷰) LLM은 제안만
   │
   ├─ 모티프 필요/교체 ─▶ present_motif_candidates (재사용 top-K, 무료)
   │      ├─ 사용자 select_motif(기존)      → 무료 즉시 커밋 → designing
   │      └─ 사용자 "새로 생성" 선택        → [confirm_generate] → generating_motif [Recraft, 비쌈]
   │                                              → normalize/register → 후보 합류 → 재선택
   ├─ 후보 준비됨(ready) ── 사용자 계속 편집 → designing
   └─ 후보 확정 ─▶ [finalize = 버튼(UX 결정, 무료)] → 결정론 텍스처 렌더
```
- `generating_motif`·`photorealizing`로 가는 엣지는 **확인 이벤트가 있어야만** 열린다. LangGraph의
  interrupt/human-in-the-loop로 모델링(확인 대기 = checkpoint).

### 8.3 모티프 선택 & 재생성 승인 (S12) — 사용자가 선택
- `swap_motif`/`add_layer(motif)`/신규 authoring의 motif spec은 먼저 **재사용 후보**를 만든다:
  `resolve_motifs`의 exact descriptor + `scope` 하드필터 + 임베딩 유사도(모두 **무료/저비용**, Recraft 미호출)
  로 pool에서 **top-K 후보**를 뽑아 사용자에게 제시(썸네일 + 유사도). "**새 모티프 생성**" 옵션에는 **비용/
  시간 힌트**를 붙인다.
- **사용자가 기존 후보 선택** → 해당 `motif_id`를 intent에 freeze → 엔진(무료·즉시).
- **사용자가 "새로 생성" 선택**, 또는 채팅으로 "다른 꽃으로 다시 만들어"(=`force_new`) →
  **`confirm_generate` 승인 스텝** → Recraft 생성(비쌈) → `sanitize`+`normalize_motif_svg`+구조검사(D14) →
  후보 풀 합류 → 사용자 재선택.
- 현행 **generate-on-miss 자동**(D5·D14)은 **인터랙티브 세션 경로에서 이 게이트로 대체**한다. 내부/배치
  (`intent` 직접) 경로는 기존 자동 유지 가능(§16 확인).
- **결정론 유지**: 변형 다양성은 여전히 `variant_group + seed`의 순수 함수(변형 샘플링). 후보 제시·선택은
  UX 계층 사건이고, 일단 `motif_id`가 확정되면 엔진 계약은 그대로다.

### 8.4 실사화 트리거 — UX 결정 단계 (비용 게이트 아님)
- 실사화(원단 텍스처 렌더)는 **결정론·무료·로컬**([photoreal-fabric-render.md](photoreal-fabric-render.md))이라
  **비용 승인이 아니다.** finalize는 "이 후보로 결정 → 텍스처 렌더"라는 **UX 결정 버튼**이다(사용자가 후보를
  명시적으로 고름; LLM이 문장을 finalize로 자동 분류하지 않음).
- 세션은 `current_candidates`에서 확정 후보의 SVG를 넘기는 **finalize 노드**만 담당.

### 8.5 결정론 비용 가드 (S13) — LLM 무관
- **세션당 예산·레이트리밋**: Recraft 생성 횟수·실사화 횟수 상한(초과 시 확인에도 거부/경고). resource-ceiling
  (README) 정신의 결정론 상한.
- **in-flight lock**: 같은 motif spec/같은 승인 타일이 생성 중이면 중복 호출 금지(dedup).
- **비용/시간 힌트**를 확인 UI에 노출. 지출은 항상 사용자가 본 뒤 누른다.
- (옵션) "자동 승인 + 예산 상한" 모드로 완전 자동 흐름도 지원 가능 — **기본은 확인 게이트**.

---

## 9. 결정론 계약 (불변식)

- 세션은 비결정(LLM tool 선택)이나 **각 턴 커밋 `current_intent`는 재현 가능**:
  `(intent, seed, colorway, registry_version) → byte-identical SVG`(기존, `tests/test_determinism.py`).
- `apply_tools`는 순수 함수 — 현재 시각·무작위·dict 순서 의존 금지(CLAUDE.md 코드 규칙).
- `resolve_motifs`·Recraft·LLM tool 선택·실사화는 엔진 경계 **밖**. concrete motif_id로 freeze된 뒤에만 엔진 진입.
- **edit-as-delta 이득**: 팔레트 한 슬롯만 바꾸면 그 필드만 바뀌어 무관한 SVG는 그대로(drift 제거).

---

## 10. API 표면 변경 (S8)

- `POST /api/v1/generate`
  - optional `session_id` 추가. 없으면 현행(신규·stateless 호환), 있고 세션 존재면 `prompt`=편집 지시(§6.3).
  - 응답: 기존 slim shape + `session_id`, (선택) `turn_id`/`checkpoint_id`, **`pending`**(모티프 후보 제시 or
    확인 대기 시).
- **모티프 선택/승인**(§8.3): `POST /sessions/{id}/select-motif {layer_id, motif_id}` 및
  `POST /sessions/{id}/confirm {action: generate_motif|finalize, ...}` (명칭 §16). 구조화 액션 — 자유 텍스트 아님.
- `GET /api/v1/sessions/{id}`: 대화 이력 + `current_intent`(노출 정책 §16) + 후보 이력 복원.
- 분기/undo(S9): `from_checkpoint` 옵션 — **MVP 이후**.
- 에러 정책은 기존 매핑 준수(422·502, `X-Request-ID` 전파).

---

## 11. 영속화 (S7)

- 세션 상태를 `SUPABASE_DB_URL`로 저장(스키마 소유는 **React 모노레포** — 이 레포 client-only, DDL 금지).
  필요 테이블(예: `design_sessions`) 정의는 모노레포에서.
- 미설정 시 **in-memory degrade**(`generation_log` no-op 패턴 동형). LangGraph checkpointer 백엔드를 여기 매핑.
- `SUPABASE_DB_URL`은 서버 사이드 전용·RLS 우회 — 클라이언트 노출 금지.

---

## 12. 관측성 (S6 — 선택)

- 기존 request-id 전파 + 요청당 `log_metrics()` 1줄(stdlib) 유지.
- 멀티턴이 되면 **LangSmith run tree**로 세션 재생·실패 프롬프트 추적·세션당 토큰/비용·편집 성공률 측정 가능.
- 단 SaaS egress + motif IP 민감도([[motif-provenance-ip-model]]): `langchain`/`langgraph` **없이** `langsmith`
  단독, **authoring 구간에만** 계측(엔진 경로 금지), egress 승인·서버 사이드 키·키 미설정 시 no-op.
- 선행 대안: 기존 `log_metrics()`에 `model`·토큰·`llm_ms` 필드 얹는 로컬 계측(~15줄)으로 대부분 커버.

---

## 13. 프레임워크 채택 근거 (정직)

- **LangGraph — 채택.** 값을 하는 지점은 단순 "마지막 intent 유지"(session dict면 충분)가 아니라
  **분기(undo/redo/fork)·checkpoint·resume**와 **확인 게이트(human-in-the-loop, §8)**를 first-class로 만드는 것.
  제품 목표가 대화형 스튜디오이므로 처음부터 LangGraph로 상태·게이트를 둔다.
- **LangChain — 부분(도구 바인딩만, S5).** tool-use 루프는 native google-genai function calling으로도 가능.
  전면 Runnable/chain 도입은 안 함(단일 호출·비RAG에 순손실). 실측 후 native로 충분하면 미도입 가능.
- **LangSmith — 선택(§12).** 멀티턴에서 정당화되나 egress 승인 전제. standalone·opt-in.
- **채택 안 함**: 엔진 그래프화, motif resolution agentic화, planner/critic(별도 검증).

---

## 14. 수용 기준

- **편집 국소성**: 턴1 생성 → 턴2 "stripe 45도로"만 → 결과 intent가 해당 `stripe.angle`만 바뀌고 나머지 동일.
- **결정론 무손상**: `.venv/bin/python -m pytest -q` 그린, 특히 `tests/test_determinism.py` 통과.
- **도구 화이트리스트 enforcement**: 화이트리스트 밖 필드/op는 적용 안 되고 검증 게이트에서 차단.
- **비용 게이트(핵심)**:
  - `swap_motif`/모티프 필요 시 **재사용 후보 제시는 무료**(Recraft 미호출) — 테스트로 봉인.
  - **Recraft 생성은 명시적 "새로 생성" 선택/`confirm_generate` 없이는 호출되지 않음**.
  - **실사화는 `confirm_finalize`(버튼) 없이는 호출되지 않음**.
  - **세션 예산 초과 시** 추가 생성/실사화가 확인에도 거부/경고. in-flight 중복 호출 dedup.
- **apply_tools 결정론**: 같은 `(이전 intent, 도구 호출 열)` → 같은 새 intent.
- **세션 복원**: `GET /sessions/{id}`로 복원한 intent 재합성이 직전과 byte 동일.
- **degrade**: `SUPABASE_DB_URL` 미설정에서도 세션이 in-memory 동작.

---

## 15. 단계 (phasing)

| 단계 | 내용 | 도구 | 체크포인트 |
|---|---|---|---|
| **P0** | 세션 상태 + edit-as-delta(도구 최소셋) + **모티프 후보 제시/선택 + 재생성 승인 게이트(§8.3)** + 실사화 버튼 트리거(§8.4) | LangGraph + tool binding(S5) | 턴2 국소 편집 + 게이트 없이 Recraft/실사화 미호출 + 결정론 그린 |
| **P1** | 세션 영속(Supabase) + `GET /sessions/{id}` + 비용 가드(예산·dedup, S13) | — | 재시작 후 복원·재현, 예산 상한 동작 |
| **P2** | 분기/undo/redo/fork(checkpoint) | LangGraph checkpointer | 되감기·fork 후보 |
| **P3** | 세션 트레이싱·비용·eval(선택) | LangSmith standalone | egress 승인 후 계측 |
| **후보** | planner/critic 멀티에이전트 | LangGraph multi-agent | 별도 spec·ROI 검증 |

**P0가 전환점** — one-shot에서 대화형으로, 그리고 비싼 작업을 게이트 뒤로. 세션 문서 분해는
`docs/plan/*` 컨벤션(목표·선행·범위·비범위·작업·파일·수용 기준·리스크)을 따른다.

---

## 16. 열린 결정 / 리스크

- **S5**: 도구 tool-use를 LangChain binding vs native google-genai function calling — 실측 후 결정.
- **S6**: LangSmith egress 승인·self-host 필요성.
- **모티프 게이트 범위**: 후보 top-K의 K, 인터랙티브 경로에서만 게이트하고 `intent` 직접/배치 경로는 자동
  generate-on-miss 유지할지.
- **세션 예산 값**(S13): 세션당 Recraft/실사화 상한 수치, 자동 승인 모드 제공 여부.
- **세션 영속 테이블**: `design_sessions` 스키마를 모노레포에 추가할지, 초기 in-memory로 P0 닫을지.
- **편집 delta 폴백**: LLM이 도구만으로 편집을 못 풀 때 전체 재작성 폴백(그 경우 drift 감수)할지.
- **current_intent 노출 정책**: `GET /sessions`가 intent를 클라이언트에 노출할지(현행 slim은 의도적으로 감춤).
- **액션 엔드포인트 명칭**: `select-motif`/`confirm`.
- **LangGraph 영속 백엔드 vs 스키마 소유권 (해결됨)**: LangGraph 공식 `PostgresSaver`는 `checkpointer.setup()`
  에서 checkpoint 테이블 DDL을 실행하는데, 이 레포는 런타임 DDL 금지·스키마 모노레포 소유가 하드 규칙이다.
  → **결정(S14): checkpoint 테이블은 모노레포 마이그레이션으로 선(先)정의**하고, 이 레포는 `SUPABASE_DB_URL`로
  **client 연결만** 한다(`setup()` 호출 금지, 앱 런타임 DDL 없음). 이 레포의 client-only 원칙과 정합.
- **buy-vs-build (종결됨 — S14)**: fork/undo가 **제품 기능으로 확정**되어 LangGraph 채택. 결정적 우위인
  fork/undo/time-travel(`get_state_history`+`update_state`)을 프레임워크로 받는다. (단일 선형 confirm만이었다면
  손구현이 더 가벼웠을 것 — 그 케이스는 배제됨.)
- **langchain 범위**: 도구 바인딩은 **langchain-core**(langgraph가 이미 핀하는 의존성) + provider 통합 패키지면
  충분. **umbrella `langchain`은 불필요**(체인/에이전트/RAG 없음). LangGraph 미채택 시 core만 따로 들일 이유도 없음.
- **리스크**: LangGraph checkpointer/state·게이트 로직이 결정론 경로로 새지 않도록 경계 테스트 필수.
  프레임워크 버전 churn은 authoring 계층에 격리(경계 원칙이 방어). bind_tools는 native function-calling에
  위임할 뿐 도구 화이트리스트/검증을 대신 해주지 않음 — 결정론·검증은 여전히 앱 책임.
