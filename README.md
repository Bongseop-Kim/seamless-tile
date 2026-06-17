# seamless tile

AI seamless SVG 생성 서비스. 목표는 텍스트 또는 참조 이미지에서
**이음매 없는(seamless) 텍스타일 패턴 SVG**를 생성하는 것이다.

현재 아키텍처의 중심은 다음 네 단계다.

```text
Primitive 생성
+ Placement 계산
+ Layer 합성
+ Seamless 보장
```

상세 설계는 [ARCHITECTURE.md](ARCHITECTURE.md)를 기준으로 한다.

## 현재 상태

기존 `/api/v1/patterns/*` 확인용 API와 완성 패턴 클래스 구조는 제거됐다. 새 엔진은
`background`, `stripe`, `motif`(circle·bee 등 모든 형상은 motif로 통합)를 완성 패턴이 아닌
primitive로 만들고, placement와 composition 단계에서 합성한다.

엔진 `generate()` 파이프라인(intent → seamless SVG candidate)은 이미 동작한다: MVP 사선 타이
intent를 `<pattern>` + `<symbol>`/`<use>` 단일 SVG로 합성하고, 구조적 seamless를 보장한다.

현재 남아 있는 API:

```text
GET /api/v1/health
GET /api/v1/palettes
```

HTTP `/api/v1/generate` 라우트는 이후 세션에서 추가한다.

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 래스터(PNG/TIFF) 출력에 필요한 SVG 렌더러
brew install librsvg
```

SVG 생성 자체는 렌더러 없이도 가능하다. PNG/TIFF export 검증에는 `rsvg-convert` 또는 `resvg`가
필요하다.

## 실행

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- 문서: `http://localhost:8000/docs`
- 헬스: `http://localhost:8000/api/v1/health`

## 목표 데이터 흐름

```text
GenerateRequest
  -> IntentBuilder
  -> Intent JSON
  -> PrimitiveFactory
  -> PlacementEngine
  -> CompositionEngine
  -> SeamlessEngine
  -> SVG candidate
```

LLM은 intent JSON까지만 만든다. SVG 좌표, 반복, 배치, 합성, seamless 보장은 결정론적 엔진이
담당한다.

## MVP 목표

첫 MVP는 LLM 없이 직접 intent를 넣어 다음 결과를 만들 수 있어야 한다.

```text
background
+ diagonal stripe layer
+ stripe lane 위 circle motif layer
+ stripe lane 위 bee motif layer
-> seamless SVG
```

핵심 규칙:

- stripe primitive는 motif를 직접 그리지 않는다.
- circle·bee 같은 motif는 `path_following` placement로 stripe lane(`lanes()` 계약) 위에 배치된다.
- 최종 SVG는 Composition engine이 layer 순서대로 `<pattern>` + `<symbol>`/`<use>`로 합성한다.
- 같은 intent·seed·colorway는 바이트 동일 SVG를 생성한다.
- seamless는 by-construction 불변식(commensurability·torus wrap·boundary clone)이 1차 보증이며,
  타일링 연속성 raster 가드를 회귀 가드로 둔다.

## 테스트

```bash
.venv/bin/pytest -q
```
