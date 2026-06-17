# 세션 3 — Placement(path_following) + Composition(`<pattern>`) + motif registry

> MVP 수직 슬라이스의 배치·합성. host `lanes()` 위에 motif를 올리고 `<pattern>`으로 단일 SVG를
> 조립한다. 4단계 분리가 닫히는지 증명.

## 선행 조건
세션 2 (stripe·lanes()·각도 스냅).

## 범위 (in-scope)
- **`motifs/registry.py`** — built-in `circle`, `bee`를 **intake 계약**으로 정규화 등록:
  `{id, symbol, bbox_mm, anchor}`. 색은 슬롯 참조(`currentColor`/indexed)로 정규화. 다색 motif는
  layer의 `colors{fill_slot -> palette_slot}`로 여러 슬롯을 바인딩한다(MVP의 circle/bee는 단색).
- **`engine/placement/path_following.py`** — host 기반 배치:
  `host_layer.lanes()` + `lane`(center/end 또는 LaneField.id) 소비 → `spacing_mm`/`phase_mm`로
  instance(좌표·`rotation: follow_path|fixed`) 생성. 좌표는 torus(타일 주기) 위에서.
- **`engine/placement/__init__.py`** — 전략 디스패치(현재 `path_following`).
- **`engine/composition.py`** — `z_order` 정렬 후:
  - motif geometry를 `<defs>`/`<symbol>`에 **1회** 정의.
  - 타일을 `<pattern patternUnits="userSpaceOnUse" width/height=tile_mm>`로 정의.
  - instance를 `<use>`로 인스턴싱. **enumerate 금지.**

## 비범위 (out-of-scope)
- 다른 placement 전략(lattice/scatter/point_set) — 세션 5.
- seamless 강제·검증·boundary clone — 세션 4.
- Recraft motif·입력 allowlist — 세션 8.

## 작업
- [ ] `registry`에 circle·bee 정규화 등록 + 계약 노출.
- [ ] `path_following`: lanes() 소비 → instance 목록.
- [ ] placement 디스패치.
- [ ] `composition`: `<symbol>`/`<defs>` + `<pattern>` + `<use>` 조립.
- [ ] 테스트.

## 만들/수정 파일
`motifs/registry.py`(신규), `engine/placement/{__init__,path_following}.py`(신규),
`engine/composition.py`(신규), `tests/test_placement_path.py`(신규),
`tests/test_composition.py`(신규).

## 수용 기준
- MVP intent(background + 사선 stripe + circle + bee)가 **`<pattern>` + `<use>` 기반 단일 SVG**로
  합성된다: 출력에 `<pattern>` 1개, motif def 1개당 `<use>` 다수, 도형 enumerate 아님(회귀 가드).
- `path_following`이 stripe 내부 구현이 아니라 `lanes()` 계약만 사용한다 — stripe 내부 표현을
  바꿔도 placement가 동작(계약 의존 테스트).
- 같은 intent → 같은 instance 좌표/순서(결정론).
- `pytest` 그린.

## 리스크
- `<symbol>` 좌표계와 mm viewBox 정합. `<use>` transform(위치·회전·scale)을 mm 기준으로 일관.
- bbox가 타일 경계를 넘는 경우는 세션 4 boundary clone 전까지 잘릴 수 있음 — 이 세션 테스트는
  경계 비침범 케이스로 한정.
