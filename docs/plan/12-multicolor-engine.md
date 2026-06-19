# 세션 12 — 멀티컬러 엔진 [P2]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/motif-library-and-multicolor.md` — §4(멀티컬러 엔진), §4.2(슬롯 확장·**굽기 금지**),
  §4.3(수용 기준), 결정 **D1·D15**, §9(결정성).
- **현재 코드**: `app/motifs/registry.py`(`normalize_motif_svg`, `_recolor_to_slot` — 현재 모든 색을
  `currentColor`로 강제, `MotifDef`), `app/engine/composition.py`(`compose`, **`:111` 멀티컬러 거부**,
  `:119/:127` `<use color=...>` 단색 바인딩, `:36/:117` `setdefault(motif.id, symbol)` dedup),
  `app/render/sanitize.py`(allowlist), `app/validate/intent.py`(`MotifParams.colors`).
- **선행 세션**: 없음(엔진 독립). 단 S9의 `color_slots` 컬럼을 활용.

## 목표
모티프의 서로 다른 색을 **슬롯으로 보존**하고, 합성 시 colorway 색을 **인스턴스 단위로 바인딩**한다.
**`<symbol>`은 colorway-무관하게 1회 정의(굽기 금지, D15)** 하여 dedup·id 계약을 유지한다.

## 선행 조건
없음(엔진 독립). S12와 S10은 병행 가능.

## 범위
- `normalize_motif_svg`: 색→`currentColor` 강제 대신 **모티프-로컬 슬롯**(`s0,s1,…`)으로 보존.
  `MotifDef.color_slots` 추가. 색→슬롯 할당은 **문서 DFS 첫 등장 순**(결정론). id 해시는 **슬롯화된 기하**
  기준(colorway 무관 → "같은 그림=같은 id" 유지).
- `compose()`: `colors` 거부(`:111`) 제거. **방식 (b)** — 슬롯별 `<g>` 분리 + 슬롯 수만큼 `<use>`를
  겹쳐 각기 `color` 주입. `<symbol>`은 colorway 무관 1회 정의·dedup 유지(§4.2-4, N2).
  - (a) per-instance 인라인은 dedup 위반이라 **폐기**. 렌더러가 (b)에서 문제 시에만 폴백 + §4.3 단서.
- `validate/intent.py`: `colors` 키가 `color_slots`를 **전부** 덮는지 + 팔레트 슬롯 존재 + **미바인딩
  슬롯 규칙**(거부 or 기본색, `currentColor` 누출 금지).
- 렌더러 호환 검증으로 (b) 확정(rsvg/resvg, 출력 크기).

## 비범위
Recraft 연결(S13), 임베딩/조회(S11). 멀티컬러 *생성 소스*는 여기서 다루지 않음 — 엔진 합성만.

## 작업 / 만들·수정 파일
- `app/motifs/registry.py`(슬롯화, `color_slots`).
- `app/engine/composition.py`(`colors` 바인딩, 방식 (b)).
- `app/validate/intent.py`(교차검증, 미바인딩 규칙).
- (신규) 멀티컬러 회귀 테스트.

## 수용 기준 (검증 가능)
1. 슬롯 N개(**N>2 포함**) 멀티컬러 SVG 등록·렌더, 각 슬롯이 `colors` 매핑대로 팔레트 색.
2. `<symbol>`은 colorway 무관 **id·본문 동일**(dedup 유지) — colorway만 바꿔도 symbol 정의 불변.
3. 미바인딩 슬롯은 규칙대로(거부/기본색), 잔존 `currentColor` 누출 없음.
4. 같은 intent/seed/colorway → **바이트 동일 SVG**(멀티컬러 회귀 가드 신규).
5. 단색 모티프(`color`) 하위호환.

## 리스크
rsvg/resvg에서 방식 (b) 동작·출력 크기(실측), 슬롯 매핑 결정성.

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + verifier.
