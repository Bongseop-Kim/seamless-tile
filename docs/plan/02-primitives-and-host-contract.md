# 세션 2 — Primitives + host geometry 계약

> `background`, `stripe` primitive와 stripe의 `lanes()` host 계약, 사선 각도 commensurate
> 스냅. placement가 의존할 인터페이스를 만든다.

## 선행 조건
세션 1 (intent 모델·색 해석·결정론 헬퍼).

## 범위 (in-scope)
- **`engine/primitives/background.py`** — 슬롯 색으로 채운 배경 SVG 조각.
- **`engine/primitives/stripe.py`** — `angle`·`period_mm`·`bands[]`로 직선 band를 그리고,
  **host 계약을 노출**: `lanes() -> [LaneField{id, centerline_path, spacing_mm, phase_mm, angle}]`.
  band 중심선(`center`)·끝선(`end`)을 lane id로 식별.
- **각도 스냅** — `snap_angle(requested) -> SnappedAngle`. 요청 사선 각도를
  타일과 commensurate한 최근접 유리 기울기로 스냅. `engine/units.py` 또는 `engine/seamless.py`.
- **`render/svg.py` 출력 인코딩(보안 베이스라인)** — 속성값 quote-escape, 텍스트 `& < >`
  이스케이프. f-string 직접 보간 제거. (전체 allowlist 하드닝은 세션 8.)
- primitive 색은 슬롯 참조 → 세션 1 `resolve_color`로 채운다.

## 비범위 (out-of-scope)
- motif/placement/composition (세션 3).
- 곡선 lane 실제 구현(세션 5). 여기선 직선 lane 우선, `centerline_path`는 곡선 확장 가능한 표현.
- 입력 allowlist·업로드 검증(세션 8).

## 작업
- [ ] `background` primitive.
- [ ] `stripe` primitive: band geometry 직렬화.
- [ ] `stripe.lanes()` — LaneField 목록 노출(id·중심선·spacing·phase·snap된 angle).
- [ ] `snap_angle()` 구현 + 세션 1 검증의 "스냅 가능성"과 정합.
- [ ] `render/svg.py` 인코딩 적용.
- [ ] 테스트.

## 만들/수정 파일
`engine/primitives/{__init__,background,stripe}.py`(신규), `engine/units.py` 또는
`engine/seamless.py`(`snap_angle`), `render/svg.py`(수정),
`tests/test_primitives.py`(신규), `tests/test_angle_snap.py`(신규).

## 수용 기준
- `stripe`가 주어진 intent로 band geometry + `lanes()` 목록을 반환한다.
- `snap_angle(-32°)`가 타일 정합 유리 기울기로 스냅되고, 스냅 각도로 lane이 정수
  횟수 후 자기 자신으로 wrap한다(테스트로 commensurability 확인).
- `background`가 슬롯/colorway 색으로 채워진다.
- SVG 직렬화가 `<`, `&`, `"` 등을 이스케이프한다(인코딩 단위 테스트).
- `pytest` 그린.

## 리스크
- `centerline_path` 표현을 곡선까지 담을 수 있게(예: 파라메트릭/`path d`) 두되, 세션 2에선 직선만
  채운다. 세션 5의 wave lane이 같은 계약을 재사용하도록 설계.
