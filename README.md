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
`stripe`, `dot`, `motif`, `background`를 완성 패턴이 아닌 primitive로 만들고, placement와
composition 단계에서 합성한다.

현재 남아 있는 API:

```text
GET /api/v1/health
GET /api/v1/palettes
```

`/api/v1/generate`는 새 engine 구조가 안정된 뒤 추가한다.

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
+ stripe lane 위 dot layer
+ stripe lane 위 bee motif layer
-> seamless SVG
```

핵심 규칙:

- stripe primitive는 dot/motif를 직접 그리지 않는다.
- dot/motif는 `diagonal_lane` placement로 stripe 위에 배치된다.
- 최종 SVG는 Composition engine이 layer 순서대로 합성한다.
- 같은 intent와 같은 seed는 같은 SVG를 생성한다.
- 결과는 seam metric으로 검증 가능해야 한다.

## 테스트

```bash
.venv/bin/pytest -q
```
