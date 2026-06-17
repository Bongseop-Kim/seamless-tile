# 세션 6 — 제품 API + 운영

> `POST /api/v1/generate` 표면, 후보 다양성/랭킹, 에러 분류·관측을 만든다.

## 선행 조건
세션 4 (generate). **세션 5 필요** — "placement 종류" 다양성 축이 5의 전략들에 의존한다. 5 없이
시작하면 다양성은 colorway·seed·layout 변형으로 제한되며, 그 경우 해당 수용 기준을 축소 적용한다.

## 범위 (in-scope)
- **`api/schemas/generate.py`** — 요청/응답 모델:
  - 요청: `{ prompt, reference_image?, canvas?, palette?, colorway?, seed?, candidate_count? }`.
  - 응답: `{ request_id, candidates: [{ id, svg, intent, layout_id, source_fidelity }] }`.
- **`api/routes/generate.py`** — 이 세션에서는 **intent 직접 입력 / stub builder** 경로로
  candidate 생성(LLM은 세션 7). 다양성 축(layout·placement 종류·colorway·seed) + 랭킹(seam
  통과·색 수·directional clustering) + `layout_id` de-dup.
- **에러·관측** — 분류(`4xx` 스키마 / `422` 시맨틱 검증 / `5xx` 렌더러·내부), 부분 성공(일부
  후보 실패 시 나머지 반환), `request_id` 로그·메트릭 전파, 단계별 지연·seam 분포·통과율 메트릭.
- **`api/routes/export.py`** — candidate SVG → PNG/TIFF 래스터 export(`render/raster.py`,
  DPI·크기 가드). SVG만으로 충분하면 명시적 비목표로 내린다.
- **`app/main.py`** — generate·export 라우터 등록.

## 비범위 (out-of-scope)
- LLM/이미지 해석(세션 7).
- 보안 하드닝·업로드 검증(세션 8) — 이 세션은 인코딩 베이스라인(세션 2)에 의존.

## 작업
- [ ] 요청/응답 스키마.
- [ ] generate 라우트(intent 직접/stub) + 후보 다양화.
- [ ] 랭킹·de-dup.
- [ ] 에러 분류 + 부분 성공.
- [ ] request_id 전파 + 메트릭 로깅.
- [ ] main.py 라우터 등록.
- [ ] 테스트(httpx).

## 만들/수정 파일
`api/schemas/generate.py`(신규), `api/routes/generate.py`(신규), `app/main.py`(수정),
`tests/test_api_generate.py`(신규).

## 수용 기준
- intent 직접 경로로 `candidate_count`개 후보를 반환하고 `layout_id`로 de-dup·랭킹 정렬한다.
  포괄 요청은 distinct `layout_id`가 ≥ min(2, 가용 전략 수)임을 단언한다.
- 검증 실패 → `422`, 렌더러 실패 → `5xx`, 일부 후보 실패 시 부분 성공 동작.
- `request_id`가 응답·로그에 일관 전파된다.
- `pytest` 그린(httpx 테스트 클라이언트).

## 리스크
- 다양성 vs 결정론: 후보 다양화는 seed/축 분기로 결정론을 유지(같은 요청·같은 seed 집합 → 같은
  후보 집합).
