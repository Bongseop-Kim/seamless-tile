# 세션 16 — 대화형 세션 + edit-as-delta + 비용 게이트 [P0]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.
> **P0 = 전환점.** one-shot → 대화형, 그리고 비싼 작업(Recraft)을 확인 게이트 뒤로.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/conversational-design-sessions.md` — §0(관통 원칙), §5(세션 상태), §6(턴 처리),
  §7(편집 도구 화이트리스트), §8(비용 게이트 상태머신), §9(결정론 계약), 결정 **S1~S14**.
- **현재 코드**: `app/api/routes/generate.py`(입력 경로 `intent>reference_image>images>prompt`·slim response),
  `app/adapters/llm.py`(`LLMClient.complete` 단일 호출 + 검증 실패 1회 재프롬프트, `build_intents`),
  `app/adapters/motif_resolver.py`(재사용/생성 라우팅), `app/engine/intent.py`(Intent 스키마),
  `app/validate/intent.py`(시맨틱 검증·repair), `app/logs/generation_log.py`(best-effort 영속화 패턴), `app/core/observability.py`.
- **선행 세션**: 엔진 MVP(1~8)·모티프 라이브러리(9~14). 실사화 핸드오프는 **세션 15**(`/finalize`).

## 목표
`session_id`를 도입해 **편집 턴**을 지원한다. 편집은 **전체 재작성이 아니라 intent delta** — LLM은 §7 **화이트리스트
도구만** tool-use로 호출하고, `apply_tools`가 **결정론 Python**으로 적용한다. 비싼 이산 작업(**Recraft 모티프 생성**)은
**명시적 사용자 선택/확인 이벤트로만** 발동한다(자동 실행 금지). LangGraph로 세션 상태·턴 그래프·확인 게이트를 모델링.

## 관통 원칙 (반드시 준수)
- **엔진은 성역.** 모든 확장은 authoring(prompt→intent)에만. 엔진 단계(candidate/placement/composition/seamless)를
  LangGraph 노드/LLM 판단으로 감싸지 않는다. 엔진은 concrete-motif가 확정된 **frozen intent**만 받는 순수 Python.
- **해석 ≠ 지출.** LLM은 제안·분류만. 돈 드는 전이는 결정론 정책 또는 명시적 확인으로만 게이트(S11).

## 범위
- **LangGraph 세션 그래프**(thread = `session_id`): `classify_turn`(신규 vs 편집) → `author_intent`(기존 `build_intents` 재사용) /
  `edit_intent`(도구 호출) → `apply_tools`(결정론 patch) → (motif 변경 시)`resolve_motifs` 게이트 → `validate_intent(repair)` → checkpoint → 엔진.
- **세션 상태 모델**(§5): `session_id, turns[], current_intent(재현 앵커), current_candidates[], pending(게이트 대기),
  seed, colorway, registry_version, budget`. 편집 컨텍스트로 LLM에 넘기는 것은 **compact summary**(전체 intent 아님), 도구 인자는 layer_id/slot_id 안정 키.
- **편집 도구 화이트리스트**(§7, S2) — 닫힌 집합, Python이 인자 검증 후 적용:
  `set_colorway, set_palette_slot, scale_motif, set_stripe, set_density, add_layer/remove_layer, swap_motif, set_seed, regenerate, set_material`.
  대부분 **어댑터 호출 0**(순수 patch, S10). `set_material`은 엔진 intent 불변 — material map은 **세션/finalize 상태**(세션 15가 소비).
- **비용 게이트 상태머신**(§8, S11~S13):
  - 모티프 필요/교체 → `present_motif_candidates`(재사용 top-K: exact + `scope` 하드필터 + 임베딩 유사도, **모두 무료, Recraft 미호출**).
  - 사용자 `select_motif(기존)` → 무료 즉시 커밋. 사용자 "새로 생성"/`force_new` → **`confirm_generate` 승인** → Recraft(비쌈) → sanitize+normalize+구조검사 → 후보 합류 → 재선택.
  - LangGraph **interrupt/human-in-the-loop**로 확인 대기(=checkpoint) 모델링.
- **실사화 트리거**(§8.4): finalize는 **UX 결정 버튼**(무료·로컬, 비용 게이트 아님) → 확정 후보 SVG/intent를 **세션 15의 `/finalize`**로 넘기는 finalize 노드만.
- **API**(S8): `POST /api/v1/generate`에 optional `session_id`(없으면 현행 stateless 호환, 있으면 `prompt`=편집 지시). 응답 = 기존 slim + `session_id`, (선택)`turn_id`, **`pending`**(후보 제시/확인 대기 시). 구조화 액션 엔드포인트: `POST /sessions/{id}/select-motif`, `POST /sessions/{id}/confirm {action}`.

## 비범위
- 세션 영속(Supabase)·`GET /sessions/{id}`·비용 가드 예산값 → **세션 17(P1)**. P0는 **in-memory** checkpointer로 닫는다.
- 분기/undo/redo/fork → **세션 18(P2)**.
- LangSmith 트레이싱(§12, 선택)·planner/critic 멀티에이전트(§15 후보) — 이번 범위 밖.
- 엔진 내부 변경, `reference_image` 경로 고도화, motif resolution의 agentic 검색 루프화(현행 deterministic cache 유지).
- 실사화 렌더 구현 자체는 세션 15. 여기선 트리거/핸드오프만.

## 작업 / 만들·수정 파일
- `app/sessions/`(신규 패키지): LangGraph 그래프 정의, 세션 상태 모델, 노드(classify/author/edit/apply_tools/resolve gate/validate/finalize), in-memory checkpointer.
- `app/sessions/tools.py`(신규): 편집 도구 화이트리스트 + 각 도구의 **결정론 Python 적용 + 인자 검증**(`validate/intent.py` 시맨틱 규칙 재사용).
- `app/adapters/llm.py`: tool-use 바인딩(**S5 실측** — LangChain `bind_tools` vs native google-genai function calling). bind_tools는 도구 선택만 위임, **화이트리스트/검증은 앱 책임**.
- `app/api/routes/generate.py`: optional `session_id` 분기 + `pending` 응답. 신규 `app/api/routes/sessions.py`(select-motif/confirm).
- `app/adapters/motif_resolver.py`: 인터랙티브 경로에서 generate-on-miss 자동을 **후보 제시 + 게이트로 대체**(D5·D14). `intent` 직접/배치 경로는 자동 유지(§16 확인).
- `requirements.txt`: `langgraph` + `langchain-core`(도구 바인딩). **umbrella `langchain` 미도입**(체인/RAG 없음).
  P0는 **in-memory saver만** 사용 — Postgres saver의 `setup()`/DDL은 금지(스키마는 모노레포 소유, 세션 17 참고).

## 수용 기준 (검증 가능)
1. **편집 국소성**: 턴1 생성 → 턴2 "stripe 45도로"만 → 결과 intent의 `stripe.angle`만 바뀌고 나머지 동일.
2. **도구 화이트리스트 enforcement**: 화이트리스트 밖 필드/op는 적용 안 되고 검증 게이트에서 차단.
3. **비용 게이트(핵심)**:
   - 모티프 필요 시 재사용 후보 제시는 **무료(Recraft 미호출)** — 테스트로 봉인.
   - Recraft 생성은 명시적 "새로 생성"/`confirm_generate` **없이는 호출되지 않음** — 테스트로 봉인.
   - 실사화는 `confirm_finalize`(버튼) 없이 호출되지 않음.
4. **apply_tools 결정론**: 같은 `(이전 intent, 도구 호출 열)` → 같은 새 intent(현재 시각·무작위·dict 순서 의존 금지).
5. **결정론 무손상**: `pytest -q` 그린, 특히 `tests/test_determinism.py` 통과. `(intent, seed, colorway, registry_version) → byte-identical SVG` 유지.
6. **stateless 호환**: `session_id` 없는 기존 `POST /generate` 동작·응답 불변.
7. **degrade**: `SUPABASE_DB_URL` 미설정에서도 세션이 in-memory로 동작(영속은 세션 17).

## 리스크
- LangGraph checkpointer/state·게이트 로직이 **결정론 경로로 새지 않도록** 경계 테스트 필수.
- 프레임워크 버전 churn → authoring 계층에 격리(경계 원칙이 방어).
- **S5 미확정**: tool-use를 LangChain binding vs native function calling — 실측 후 결정(둘 다 도구 검증은 대신 안 해줌).
- 편집 delta 폴백(LLM이 도구만으로 못 풀 때 전체 재작성 허용할지) — §16 열린 결정.
- 인터랙티브 게이트 범위(top-K의 K, `intent` 직접/배치 경로 자동 유지 여부) — §16 확인.

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + 작성/검토 분리 verifier. LangGraph는 authoring 어댑터 — 미설정/실패 시에도 stateless 경로 무영향.
