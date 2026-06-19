# 세션 10 — 프롬프트→모티프→intent 글루 (단색·정확매칭) [P0]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/motif-library-and-multicolor.md` — §6(오케스트레이션 글루), §6.1(2단계·정확매칭
  우선), §6.4(에러 처리), 결정 **D2·D9·D10·D11·D12·D18**.
- **아키텍처/규약**: `ARCHITECTURE.md`, `docs/plan/00-overview.md`.
- **현재 코드**: `app/adapters/llm.py`(`build_intent`, `_build_prompt` — 현재 motif_id를 등록된 것으로
  제한), `app/api/routes/generate.py`(입력 우선순위·에러 매핑 422/502), `app/motifs/registry.py`
  (`normalize_motif_svg`, `register_motif`), `app/validate/intent.py`.
- **선행 세션**: **S9(영속화)** — 모티프 조회/등록이 DB 경유여야 한다.

## 목표
`prompt` 경로에서 LLM이 **intent + 모티프 명세(들)**를 산출하고, **정확매칭 + 하드필터**로 조회하여
miss면 **LLM이 단색 SVG를 생성·등록**, concrete `motif_id`를 intent에 주입해 엔진 compose까지 잇는다.
(임베딩 없이 — D18.)

## 선행 조건
S9 완료(모티프 DB 영속화·조회).

## 범위
- `llm.build_intent` 확장: intent + **모티프 명세 리스트** 산출. `subject`·`part`는 **통제 어휘 주입 +
  어휘 외 출력 시 1회 재프롬프트 후 거부**(M2). 채팅 모델 = **Gemini 2.5 Flash-Lite/Flash**(D12).
- (신규) `app/adapters/motif_resolver.py`: **정확매칭(정규화 descriptor 완전 일치) → `subject`·`part`
  하드필터** → miss 판정. `part` 미존재 시 false-miss→생성 폴백.
- miss 시 **LLM 단색 SVG 생성** → `normalize_motif_svg` → `register_motif`(S9 경유).
- concrete `motif_id`를 해당 motif 레이어에 주입 → 엔진 compose.
- 에러 매핑(§6.4): 명세 실패/어휘 외 → 422, 생성 실패 → 502, 부분 성공 규칙.

## 비범위
임베딩 유사도·변형 샘플링(S11), 멀티컬러(S12), Recraft(S13).

## 작업 / 만들·수정 파일
- `app/adapters/llm.py`(명세 산출 + facet 검증).
- (신규) `app/adapters/motif_resolver.py`(정확매칭 + 하드필터 + 주입).
- `app/api/routes/generate.py`(글루 연동 + 에러 매핑).

## 수용 기준 (검증 가능)
1. "돼지 무늬" 프롬프트 → 미등록이면 생성·등록·합성까지 한 흐름 성공(E2E).
2. 같은 `prompt + seed` → **동일 결과**(엔진 결정성 유지).
3. facet 어휘 외 LLM 출력 → 재프롬프트 후에도 실패 시 422.
4. 생성/어댑터 실패 → §6.4대로 502 또는 부분 성공.

## 리스크
LLM 단색 SVG의 `sanitize` 통과율·품질, facet 매핑 정확도(목표값은 스펙 §12 열린 항목), 재프롬프트 비용.

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + 작성/검토 분리 verifier. 비결정 단계(LLM)는 엔진 경계 밖에서 freeze 후 투입.
