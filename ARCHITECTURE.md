# 아키텍처 — AI Seamless SVG 생성기

텍스트 또는 참조 이미지로부터 **이음매 없는(seamless) 텍스타일 패턴 SVG**를 생성하는
서비스다. 엔진의 핵심은 완성 패턴 클래스를 많이 만드는 것이 아니라, 다음 네 단계를 분리하는
것이다.

```text
Primitive 생성
+ Placement 계산
+ Layer 합성
+ Seamless 보장
```

LLM은 최종적으로 `prompt -> intent JSON` 변환만 담당한다. SVG 좌표, 반복, 배치, 합성,
seamless 보장은 모두 결정론적 엔진이 담당한다.

## 핵심 원칙

- **Primitive 우선**: stripe, dot, motif, background는 완성 패턴이 아니라 재사용 가능한 SVG
  primitive다.
- **Placement 분리**: primitive는 자신이 어디에 놓일지 모른다. grid, scatter, periodic,
  diagonal lane 같은 배치는 Placement engine이 계산한다.
- **Layer 합성 분리**: 최종 SVG는 개별 primitive가 아니라 Composition engine이 layer 순서대로
  합성한다.
- **구조적 seamless**: 픽셀 보정이 아니라 repeat lattice, pattern transform, torus wrap,
  boundary clone으로 경계 연속성을 보장한다.
- **벡터 우선**: SVG가 단일 진실 공급원이다. PNG/TIFF는 SVG를 래스터화한 파생 산출물이다.
- **mm 기반 단위**: 기하는 내부적으로 밀리미터로 다룬다. `px = round(mm / 25.4 * dpi)` 변환은
  래스터 경계에서만 수행한다.
- **외부 의존성 격리**: LLM, 저장소, CDN, Supabase 등은 코어 엔진 바깥 어댑터로 둔다.

## 제거 방향

기존 `patterns/stripe`, `patterns/dot`, `patterns/check`, `patterns/herringbone`처럼 각각이
완성 SVG 패턴을 직접 만드는 구조는 정식 아키텍처에서 사용하지 않는다.

정리 방향:

- `/api/v1/patterns/*` 확인용 API 제거
- `Pattern` ABC 기반 완성 패턴 클래스 제거
- stripe 내부에서 dot/motif를 직접 그리는 구조 제거
- 개발용 인메모리 pattern 저장소 제거
- 기존 구현은 필요한 수학/렌더링 아이디어만 새 엔진으로 옮기고 폐기

유지 가능한 자산:

- mm 단위 변환과 SVG 숫자 포맷
- 래스터 export
- seam metric 검증
- palette/colorway 유틸
- 기존 예제 SVG는 시각 회귀 fixture로만 사용

## 목표 구조

```text
app/
├── main.py
├── core/
│   └── config.py
├── engine/
│   ├── intent.py              # 엔진 입력 계약
│   ├── units.py               # mm/px 변환, SVG 숫자 포맷
│   ├── palette.py             # palette, colorway, 색상 검증
│   ├── primitives/            # stripe, dot, motif, background primitive
│   ├── placement/             # repeat, grid, periodic, scatter, diagonal_lane
│   ├── composition.py         # layers[] -> SVG tile
│   ├── seamless.py            # repeat lattice, torus wrap, boundary clone
│   └── generate.py            # intent -> candidates
├── motifs/
│   └── registry.py            # bee, flower 등 SVG defs
├── render/
│   ├── svg.py
│   └── raster.py
├── validate/
│   └── seamless.py
└── api/
    ├── routes/generate.py
    ├── routes/export.py
    └── schemas/generate.py
```

## 엔진 데이터 흐름

```text
GenerateRequest
  -> IntentBuilder
  -> Intent JSON
  -> PrimitiveFactory
  -> PlacementEngine
  -> CompositionEngine
  -> SeamlessEngine
  -> SVG candidate
  -> Validation/export/storage adapters
```

각 단계의 책임은 겹치면 안 된다.

- **PrimitiveFactory**: stripe, dot, motif, background 같은 그릴 수 있는 SVG 조각을 만든다.
- **PlacementEngine**: primitive instance들의 좌표, 회전, scale, 반복 위치를 계산한다.
- **CompositionEngine**: layer 순서, opacity, blend, clipping, SVG defs/use를 조립한다.
- **SeamlessEngine**: repeat mode와 torus wrap을 적용해 경계 연속성을 보장한다.

## Intent 계약

최종 제품 API는 SVG를 직접 요청하지 않는다. 사용자는 prompt, 참조 이미지, canvas, palette,
seed 정도만 전달한다. 엔진은 이 입력을 intent JSON으로 변환해 실행한다.

```jsonc
{
  "canvas": {
    "tile_mm": 48
  },
  "seed": 184231,
  "palette": ["#10243a", "#ef8a7a", "#f5ca57"],
  "layers": [
    {
      "id": "ground",
      "type": "background",
      "params": { "color": "#10243a" }
    },
    {
      "id": "stripe_base",
      "type": "stripe",
      "params": {
        "angle": -32,
        "period_mm": 24,
        "bands": [
          { "offset_mm": 6, "width_mm": 12, "color": "#0a1a2b" }
        ]
      }
    },
    {
      "id": "dot_on_stripe",
      "type": "dot",
      "params": {
        "shape": "circle",
        "size_mm": 1.4,
        "color": "#ef8a7a"
      },
      "placement": {
        "type": "diagonal_lane",
        "host_layer": "stripe_base",
        "lane": "center",
        "spacing_mm": 6,
        "phase_mm": 0
      }
    },
    {
      "id": "bee_on_stripe",
      "type": "motif",
      "params": {
        "motif_id": "bee",
        "size_mm": 5,
        "color": "#f5ca57"
      },
      "placement": {
        "type": "diagonal_lane",
        "host_layer": "stripe_base",
        "lane": "end",
        "spacing_mm": 24,
        "phase_mm": 12,
        "rotation": "follow_lane"
      }
    }
  ]
}
```

중요한 규칙:

- `stripe`는 dot이나 motif를 직접 그리지 않는다.
- `dot`은 자신이 어느 stripe 위에 올라가는지 모른다.
- `motif`는 자신이 몇 개 배치되는지 직접 결정하지 않는다.
- `placement.host_layer`가 layer 간 관계를 표현한다.
- 같은 intent와 같은 seed는 같은 SVG를 생성해야 한다.

## Placement 모델

Placement engine은 primitive instance 목록을 만든다.

지원 목표:

- **grid**: 정규 격자 배치
- **periodic**: x/y 주기 기반 반복 배치
- **scatter**: seed 기반 산포 배치
- **diagonal_lane**: stripe 같은 host layer의 lane을 따라 배치

`diagonal_lane`은 “선 위에 도트/모티프를 올리는” 핵심 모델이다. stripe primitive의 angle,
period, band/lane 정보를 참조해 lane 중심선을 계산하고, `spacing_mm`, `phase_mm`, `lane`,
`offset_mm`에 따라 instance를 만든다.

## Layer 합성

Layer는 다음 정보를 가진다.

```text
id
type
params
placement
z_order
opacity
clip
```

Composition engine은 layer를 `z_order` 순서로 정렬한 뒤 하나의 SVG tile로 합성한다. SVG defs와
`<use>`는 Composition engine이 관리한다. primitive 구현이 최종 `<svg>` 문서를 직접 만들면 안 된다.

## Seamless 보장

Seamless는 각 primitive가 알아서 해결하는 문제가 아니다. 엔진 전체의 공통 책임이다.

보장 방식:

- tile 크기와 placement period를 commensurate하게 검증
- repeat lattice 기반의 반복 가능한 좌표만 허용
- bbox가 타일 경계를 넘는 instance는 반대편에 boundary clone 생성
- scatter는 torus 좌표계에서 계산
- diagonal lane은 tile 주기와 lane spacing의 정수배 조건을 검증
- 최종 SVG는 seam metric으로 검증 가능해야 함

## 제품 API

최종 제품 표면은 하나다.

```text
POST /api/v1/generate
  body: {
    prompt: string,
    reference_image?: image,
    canvas?,
    palette?,
    seed?,
    candidate_count?
  }
  -> {
    request_id,
    candidates: [
      { id, svg, intent, layout_id }
    ]
  }
```

- `reference_image`가 없으면 text-to-image 경로로 처리한다.
- `reference_image`가 있으면 image-to-image 경로로 처리하되, 참조 이미지는 스타일, 모티프, 색을
  intent JSON으로 해석하는 데만 사용한다.
- 포괄 요청은 여러 compatible layout 후보를 반환한다.
- 구체 요청은 `candidate_count=1`로 줄일 수 있다.
- LLM adapter는 intent JSON을 만들 뿐, SVG 좌표나 raw SVG를 만들지 않는다.

## 개발 순서

1. **아키텍처 정리**: 기존 pattern API와 완성 패턴 클래스 제거 방향 확정.
2. **Intent schema**: `canvas`, `layers`, `params`, `placement`, `seed`, `palette` 계약 정의.
3. **PrimitiveFactory**: background, stripe, dot, motif primitive 구현.
4. **Motif registry**: MVP motif로 `bee` 추가.
5. **PlacementEngine**: periodic, grid, diagonal_lane 먼저 구현.
6. **CompositionEngine**: layer 순서, SVG defs/use, opacity, clipping 합성.
7. **SeamlessEngine**: period 검증, torus wrap, boundary clone 공통화.
8. **Generate API**: 초기에는 LLM 없이 규칙 기반 intent builder로 연결.
9. **검증**: SVG 구조 테스트, 2x2 repeat preview, seam metric 테스트.
10. **어댑터**: storage, LLM, image-to-image는 엔진 계약 안정화 이후 추가.

## MVP 성공 기준

첫 MVP는 다음 intent를 LLM 없이 직접 넣어 생성할 수 있어야 한다.

```text
background
+ diagonal stripe layer
+ stripe lane 위 dot layer
+ stripe lane 위 bee motif layer
-> seamless SVG
```

성공 조건:

- stripe primitive와 dot primitive가 서로 독립적으로 존재한다.
- dot/motif는 stripe 내부 옵션이 아니라 `diagonal_lane` placement로 올라간다.
- 최종 결과는 Composition engine이 하나의 SVG로 만든다.
- 같은 seed와 같은 intent는 같은 SVG를 만든다.
- raster seam metric을 통과한다.
