# 세션 13 — Recraft 연결 + 적합성 게이트 + 라우팅 [P2]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/motif-library-and-multicolor.md` — §6.2(생성 소스 라우팅 + Recraft 적합성 게이트),
  결정 **D8·D11**, §4(멀티컬러 — 산출물이 슬롯화돼야 함).
- **현재 코드**: `app/adapters/recraft.py`(`create_motif` = 생성→normalize→register),
  `app/adapters/motif_resolver.py`(S10), `app/render/sanitize.py`(gradient/filter/raster **거부**),
  `app/motifs/registry.py`(`normalize_motif_svg`).
- **선행 세션**: **S10**(글루), **S12**(멀티컬러 엔진 — Recraft 멀티컬러를 살리려면 필요).

## 목표
detailed/멀티컬러 명세를 **Recraft**로 생성·정규화·등록한다. **적합성 게이트**로 sanitizer가 거부하는
요소를 차단/평탄화한다. `complexity` 기반 생성 소스 라우팅(D11)을 붙인다.

## 선행 조건
S10(글루), S12(멀티컬러 엔진).

## 범위
- `recraft.create_motif` 연결: detailed 명세 → Recraft SVG → `normalize_motif_svg`(슬롯화, S12) →
  `register_motif`(S9).
- **적합성 게이트(M1, §6.2)**: ① `gradient`/`filter`/`clipPath`/raster를 path-only로 **평탄화 시도**,
  ② 색 수 **상한 N슬롯** 초과 시 양자화 또는 거부, ③ `sanitize` 실패 → **재생성 1회 → 그래도 실패면
  거부·폴백**.
- 라우팅(D11): LLM이 명세에 `complexity(simple|detailed)` 산출 → `simple→LLM`(S10),
  `detailed/멀티컬러→Recraft`. 기본 LLM, 명시 오버라이드 가능.

## 비범위
head 카탈로그/풀 성숙(S14), 임베딩(S11).

## 작업 / 만들·수정 파일
- `app/adapters/recraft.py`(연결 + 적합성 게이트).
- `app/adapters/motif_resolver.py`(라우팅 분기).

## 수용 기준 (검증 가능)
1. detailed 명세 → Recraft 멀티컬러 모티프로 합성 성공(슬롯 보존).
2. 부적합 SVG(gradient/filter/raster) → 게이트에서 평탄화 또는 거부, **sanitize 통과분만 등록**.
3. 라우팅이 `complexity`대로 동작(simple→LLM, detailed→Recraft), 오버라이드 동작.
4. **실측**: 샘플 X개 중 `sanitize` 통과율 ≥ Y%(기준값은 스펙 §12에서 확정).

## 리스크
Recraft 산출물의 `sanitize` 통과율(**FRAGILE 가정** — 실측 필요), 색 수 폭발(슬롯 상한), 지연/비용.

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + verifier. Recraft는 어댑터(미설정 시 5xx).
