# 세션 4 — Seamless 보장 + generate 파이프라인 (MVP 닫기)

> 구조적 seamless를 강제·검증하고 `intent -> candidate` 파이프라인을 완성한다. **MVP 체크포인트.**

## 선행 조건
세션 3 (path_following·composition·registry).

## 범위 (in-scope)
- **`engine/seamless.py`** — SeamlessEngine:
  - commensurability 강제(`period | tile`, lane spacing 정수배, snap된 각도).
  - torus wrap(`x mod tile_w` 등 정수 격자 모듈러).
  - **boundary clone**: bbox가 타일 경계를 넘는 instance를 ±`tile_w`/±`tile_h`(코너 포함 최대
    4사본) **동일 `<symbol>`의 `<use>`** 로 타일 콘텐츠에 추가(geometry 복제 금지).
- **`validate/seamless.py` 보강** — by-construction 어서션 + `edge_seam` 회귀 가드(임계값 +
  per-channel 허용오차). `seamless_diff`는 디버그 보조로 유지.
- **`engine/generate.py`** — `intent -> validate -> primitives -> placement -> composition ->
  seamless -> SVG candidate(+재현 메타)`. 검증 렌더러·DPI 핀(`render/raster.py` 사용).

## 비범위 (out-of-scope)
- 제품 API·다양성/랭킹(세션 6).
- 추가 placement·대칭(세션 5).

## 작업
- [ ] SeamlessEngine: commensurability 강제 + torus 좌표.
- [ ] boundary clone(코너 4사본, `<use>` 기반).
- [ ] `edge_seam` 회귀 가드 + by-construction 어서션.
- [ ] `generate.py` 파이프라인 + 재현 메타.
- [ ] 검증 렌더러·DPI 핀.
- [ ] 테스트(MVP·결정론·clone).

## 만들/수정 파일
`engine/seamless.py`(신규), `engine/generate.py`(신규), `validate/seamless.py`(보강),
`tests/test_seamless_mvp.py`(신규), `tests/test_determinism.py`(신규).

## 수용 기준 (MVP)
- MVP intent → **seamless SVG**. by-construction 불변식 통과 + 래스터 `edge_seam ≤ 2.0`
  (per-channel mean, 핀 렌더러·300dpi; 첫 실행에서 골든 베이스라인으로 보정·고정).
- **결정론**: 같은 seed·intent·colorway → 바이트 동일 SVG.
- 타일 경계를 넘는 motif가 반대편에 clone되어 seam이 연속(렌더 후 edge 비교 테스트).
- 출력이 `<pattern>` 기반·enumerate 아님(회귀 가드 유지).
- `pytest` 그린.

## ARCHITECTURE.md 대응 MVP 성공 기준
- [x] stripe·motif primitive 독립 존재 (세션 2·3)
- [x] circle/bee는 `path_following` placement로 올라감 (세션 3)
- [x] path_following은 `lanes()` 계약에만 의존 (세션 3)
- [x] 사선 각도 commensurate 스냅 (세션 2)
- [x] Composition이 `<pattern>`+`<use>` 단일 SVG (세션 3)
- [x] 같은 seed·intent·colorway → 같은 SVG (이 세션)
- [x] by-construction + edge match 통과 (이 세션)

## 리스크
- 렌더러별 AA·서브픽셀 차이로 `edge_seam` 임계값이 환경 의존적 — 렌더러/버전/DPI 핀 필수.
- boundary clone과 `<pattern>` 클리핑 상호작용 — clone이 타일 박스 안에 들어오는지 확인.
