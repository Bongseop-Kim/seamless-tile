# 세션 5 — 커버리지·대칭·scatter

> 사선 외 계열로 추상화 일반성을 증명(과적합 반증)하고 repeat 어휘를 확장한다.

## 선행 조건
세션 4 (MVP 닫힘).

## 범위 (in-scope)
- **`engine/placement/lattice.py`** — 기저벡터 2개 격자. 기존 `placement/repeat.py`의
  block/half_drop/brick 흡수. drop을 `drop_fraction`(1/2·1/3·1/4)으로 일반화.
- **`engine/placement/scatter.py`** — blue-noise/Poisson-disk(torus, seed로 결정론) +
  sateen(N-end step)으로 정렬선·군집 회피.
- **`engine/placement/point_set.py`** — 명시 앵커점 배치(격자 교차점 등).
- **`engine/seamless.py` 대칭 보강** — mirror/reflect(super-tile 2×1·2×2에 미러 사본 bake 후
  block 타일링; `<pattern>` 네이티브 미지원 대응), glide/ogee 기초.
- **곡선 lane** — `path_following`의 `path.kind: "wave"`(자립 path) 및 host 곡선 `centerline_path`.
  토러스 주기(wavelength | tile) 검증. `centerline_path` 표현(파라메트릭/`path d`)은 세션 2에서
  확정된 것을 소비한다.

## 비범위 (out-of-scope)
- 제품 API·어댑터(세션 6·7).
- 17 wallpaper group 전체(여기선 부분집합: block/half-drop/mirror/glide).

## 작업
- [ ] `lattice` 전략(+repeat.py 흡수, drop_fraction).
- [ ] `scatter`(blue-noise + sateen, torus).
- [ ] `point_set`.
- [ ] mirror super-tile bake + glide/ogee 기초.
- [ ] `path_following` 호 길이 순회를 곡선 `centerline_path`까지 일반화(세션 3은 직선만 소비).
- [ ] 곡선 lane(wave) + 주기 검증.
- [ ] 테스트.

## 만들/수정 파일
`engine/placement/{lattice,scatter,point_set}.py`(신규), `engine/placement/repeat.py`(흡수/삭제),
`engine/seamless.py`(대칭 보강), `engine/placement/path_following.py`(wave 확장),
`tests/test_lattice.py`·`tests/test_scatter.py`·`tests/test_mirror.py`(신규).

## 수용 기준
- **비사선 올오버 모티프 계열**(scatter 또는 lattice) intent가 seamless SVG로 생성된다 — 같은
  엔진으로 사선 외 계열을 만들어 과적합을 반증.
- mirror 대칭이 super-tile bake로 seam 연속(렌더 edge 테스트).
- sateen이 동일 행/열에 2개 이상 정렬되지 않는 분포를 결정론적으로 생성(정렬 카운트 = 0 단언).
- 곡선 lane이 토러스 주기 조건을 만족하고 seam 연속.
- `pytest` 그린(결정론·seam 회귀 가드 포함).

## 리스크
- blue-noise의 torus 결정론 — seed만으로 재현되도록 RNG 경로·좌표 생성 순서 고정(바이트 동일 보장).
- mirror super-tile이 타일 크기를 2배로 키움 → `<pattern>` width/height·commensurability 재계산.
- `repeat.py` 흡수/삭제 전에 세션 4(MVP)가 `repeat.py`를 import하지 않는지 확인 — import하면 먼저
  `lattice`로 마이그레이션 후 삭제.
- 범위가 큰 세션이다(전략 3종 + 대칭 + 곡선 lane). 필요시 5a(lattice/point_set/scatter)·
  5b(mirror/glide·곡선 lane)로 분할 가능.
