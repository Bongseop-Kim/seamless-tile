# 세션 1 — Intent 계약 + 색 모델

> 엔진 입력 계약과 색 해석을 확정하고, stage-0 검증·결정론 토대를 만든다. 이후 모든 세션이
> 소비하는 척추(spine).

## 선행 조건
없음.

## 범위 (in-scope)
- **`engine/intent.py`** — intent 타입 모델(dataclass/pydantic):
  `intent_version`, `canvas{tile_mm, dpi}`, `seed`, `production{method, max_colors}`,
  `palette.slots[{id, hex, spot?, name?}]`, `colorways[{id, name, mapping}]`,
  `layers[{id, type, params, placement?, z_order, opacity?, clip?}]`.
  layer `params`의 색은 단색 `color`(슬롯 id) 또는 다색 motif용 `colors{fill_slot -> palette_slot}`로 표현한다.
- **`validate/intent.py`** — stage-0 검증:
  - 구조: JSON Schema.
  - 시맨틱: `host_layer` 참조 존재, `period_mm | tile_mm`, `lane_spacing × k = tile_period`
    정수해, 요청 각도의 p/q 스냅 **가능성** 판정(공통 규약의 스냅 정책 참조), 곡선 lane
    wavelength의 tile 정수분할, 색 슬롯 참조 유효성, **각 colorway 해석 색 수 ≤ `max_colors`**,
    `canvas.dpi ∈ {150, 300, 600}`, 값 범위(음수 spacing 등).
  - 가멋 경고: colorway 색이 CMYK/스팟 가멋을 벗어나면 **경고**(차단 아님)를 수집한다.
  - 복구: 안전값 클램프 1회. 실패 시 `IntentInvalid`(→ 후속 세션에서 422 매핑).
- **`engine/palette.py` 확장** — 색 슬롯 모델, colorway 해석(**출력색은 활성 colorway
  `mapping`으로 결정**, 슬롯 hex는 미리보기), `default` colorway 필수·fallback. 기존 `Colorway`
  모듈러 인덱싱은 슬롯 해석 유틸로 정리.
- **결정론 헬퍼** — 안정 정렬 키(`z_order` → `id`), seed 기반 RNG 팩토리, 재현 메타 구조체
  (`intent_version·engine_version·registry_version·seed·colorway_id·layout_id`).
- **`core/config.py`** — `engine_version`·`registry_version` 상수와 기본값(`dpi=300` 등). 재현
  메타가 여기서 버전을 읽는다.

## 비범위 (out-of-scope)
- 도형/배치/합성/렌더 (세션 2~4). 여기선 데이터·검증·해석만.
- 각도 스냅의 **실제 적용**(세션 2). 여기선 "스냅 가능 여부 + 목표 각도 계산"만.

## 작업
- [ ] intent 모델 정의 + 직렬화/역직렬화.
- [ ] JSON Schema 작성 + 구조 검증.
- [ ] 시맨틱 검증 규칙 구현(위 목록).
- [ ] 클램프 복구 + `IntentInvalid` 예외.
- [ ] 색 슬롯/colorway 해석 함수(`resolve_color(slot_id, colorway) -> hex|spot`).
- [ ] 결정론 헬퍼(정렬 키·RNG·재현 메타).
- [ ] 테스트.

## 만들/수정 파일
`engine/intent.py`(신규), `validate/intent.py`(신규), `engine/palette.py`(수정),
`tests/test_intent.py`(신규), `tests/test_colorway.py`(확장).

## 수용 기준
- MVP 타이 intent 픽스처가 검증을 통과한다.
- 잘못된 intent가 정확히 걸린다: 없는 `host_layer`, `period`가 `tile`의 약수가 아님, 색 수 초과,
  음수 spacing → 각각 `IntentInvalid` 또는 클램프.
- 같은 intent를 두 번 해석하면 정렬·색 해석 결과가 동일(결정론 단위 테스트).
- `resolve_color`가 colorway 교체 시 다른 색을 반환하고, `default` fallback이 동작한다.
- `pytest` 그린.

## 리스크
- 각도 스냅 정책은 **공통 규약(00-overview)의 단일 정의**를 따른다. 세션 1은 "스냅 가능성"만
  판정하고 실제 스냅은 세션 2 — 재정의 금지.
- 기존 `Colorway`(모듈러 인덱싱)를 슬롯 모델로 바꾸면 `tests/test_colorway.py`의 기존 단언이 깨질
  수 있다 — 회귀가 아니라 의도된 변경으로 갱신한다.
