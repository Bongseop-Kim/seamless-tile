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

> 도메인 전제: 주 사용처는 **넥타이 원단**이다. 따라서 패턴의 기본 방향은 **사선**이며,
> 직선(수직/수평) 방향은 사용자가 명시적으로 요청한 경우에만 쓴다.

## 핵심 원칙

- **Primitive 3종 (background / stripe / motif)**: 완성 패턴이 아니라 재사용 가능한 SVG
  조각이다. 그릴 수 있는 모든 형상 — dot·circle 같은 단순 도형부터 paisley boteh 같은 복잡
  도형까지 — 은 전부 `motif`로 통합한다. `stripe`는 직선/곡선 band를 그리는 동시에, placement가
  따라갈 lane 중심선(lane field)을 노출하는 primitive다.
- **모양과 배열의 분리**: motif는 "무엇을 그리는가"만 책임진다. mirror·ogee·half-drop 같은
  *배열 대칭*은 motif가 아니라 Seamless/Placement의 책임이다. 예를 들어 다마스크의 "반사 마름모"
  틀은 격자 대칭이고, 그 틀 안에 놓이는 형상이 motif다.
- **Placement는 계약에만 의존**: Placement는 구체 primitive 타입이 아니라 host가 노출하는
  geometry 계약(bbox·anchor·lane field)에만 의존한다. (primitive를 재사용하고, primitive를
  건드리지 않고 placement 전략을 추가하기 위한 규율이다.)
- **Layer 합성 분리**: 최종 SVG는 개별 primitive가 아니라 Composition engine이 layer 순서대로
  합성한다.
- **구조적 seamless**: 픽셀 보정이 아니라 repeat lattice, 대칭 연산(mirror/glide),
  pattern transform, torus wrap, boundary clone으로 경계 연속성을 보장한다.
- **결정론**: 같은 `intent_version`·intent·seed·colorway는 항상 같은 SVG를 만든다. motif
  생성(Recraft 등)은 런타임이 아니라 authoring-time에 수행하고 안정적 `motif_id`로 캐시한다.
- **벡터 우선**: SVG가 단일 진실 공급원이다. PNG/TIFF는 SVG를 래스터화한 파생 산출물이다.
- **mm 기반 단위**: 기하는 내부적으로 밀리미터로 다룬다. `px = round(mm / 25.4 * dpi)` 변환은
  래스터 경계에서만 수행한다.
- **외부 의존성 격리**: LLM, motif 생성기(Recraft), 저장소, CDN, Supabase 등은 코어 엔진 바깥
  어댑터로 둔다.

## 목표 구조

```text
app/
├── main.py
├── core/
│   └── config.py
├── engine/
│   ├── intent.py              # 엔진 입력 계약 + intent_version
│   ├── units.py               # mm/px 변환, SVG 숫자 포맷
│   ├── palette.py             # 색 슬롯, colorway, 색상 검증
│   ├── primitives/            # background, stripe(lane field), motif
│   ├── placement/             # lattice, point_set, path_following, scatter
│   ├── composition.py         # layers[] -> <pattern> 기반 SVG tile
│   ├── seamless.py            # repeat lattice, 대칭 연산, torus wrap, boundary clone
│   └── generate.py            # intent -> candidates (다양성/랭킹)
├── motifs/
│   └── registry.py            # motif def 정규화·캐시 (Recraft 생성물 포함)
├── render/
│   ├── svg.py
│   ├── raster.py
│   └── sanitize.py            # SVG 직렬화 인코딩, 입력 allowlist
├── validate/
│   ├── intent.py              # stage-0 검증·복구
│   └── seamless.py            # edge match / seam metric
└── api/
    ├── routes/generate.py
    ├── routes/export.py
    └── schemas/generate.py
```

## 엔진 데이터 흐름

```text
GenerateRequest
  -> IntentBuilder (LLM adapter)
  -> Intent JSON (intent_version)
  -> IntentValidator (stage-0: 검증·복구)
  -> PrimitiveFactory
  -> PlacementEngine
  -> CompositionEngine      (<pattern> + <symbol>/<use>)
  -> SeamlessEngine
  -> SVG candidate
  -> Validation(seam)/sanitize/export/storage adapters
```

각 단계의 책임은 겹치면 안 된다.

- **IntentValidator**: LLM이 만든 intent를 엔진에 넘기기 전에 구조·시맨틱을 검증하고 복구한다.
- **PrimitiveFactory**: background, stripe, motif 같은 그릴 수 있는 SVG 조각을 만들고, host는
  자신의 geometry 계약(lane field 등)을 노출한다.
- **PlacementEngine**: primitive instance들의 좌표, 회전, scale, 반복 위치를 torus 좌표계에서
  계산한다. host의 계약에만 의존한다.
- **CompositionEngine**: layer 순서, opacity, blend, clipping을 적용하고 `<pattern>` +
  `<symbol>`/`<use>`로 조립한다.
- **SeamlessEngine**: repeat lattice·대칭 연산·torus wrap·boundary clone으로 경계 연속성을
  보장한다.

## Intent 계약

최종 제품 API는 SVG를 직접 요청하지 않는다. 사용자는 prompt, 참조 이미지, canvas, palette,
seed 정도만 전달한다. 엔진은 이 입력을 intent JSON으로 변환해 실행한다.

```jsonc
{
  "intent_version": 1,
  "canvas": {
    "tile_mm": 48,
    "dpi": 300
  },
  "seed": 184231,
  "production": {
    "method": "digital",   // digital | screen
    "max_colors": 12
  },
  "palette": {
    "slots": [
      { "id": "ground", "hex": "#10243a", "spot": "19-4024 TCX", "name": "navy" },
      { "id": "accent", "hex": "#ef8a7a" },
      { "id": "gold",   "hex": "#f5ca57" }
    ]
  },
  "colorways": [
    { "id": "default", "mapping": { "ground": "#10243a", "accent": "#ef8a7a", "gold": "#f5ca57" } }
  ],
  "layers": [
    {
      "id": "ground",
      "type": "background",
      "z_order": 0,
      "params": { "color": "ground" }
    },
    {
      "id": "stripe_base",
      "type": "stripe",
      "z_order": 1,
      "params": {
        "angle": -32,              // 엔진이 tile-commensurate 각도로 스냅한다
        "period_mm": 24,
        "bands": [
          { "offset_mm": 6, "width_mm": 12, "color": "accent" }
        ]
      }
    },
    {
      "id": "circle_on_stripe",
      "type": "motif",
      "z_order": 2,
      "opacity": 1.0,
      "params": {
        "motif_id": "circle",
        "size_mm": 1.4,
        "color": "accent"
      },
      "placement": {
        "type": "path_following",
        "host_layer": "stripe_base",
        "lane": "center",
        "spacing_mm": 6,
        "phase_mm": 0
      }
    },
    {
      "id": "bee_on_stripe",
      "type": "motif",
      "z_order": 3,
      "params": {
        "motif_id": "bee",
        "size_mm": 5,
        "color": "gold"
      },
      "placement": {
        "type": "path_following",
        "host_layer": "stripe_base",
        "lane": "end",
        "spacing_mm": 24,
        "phase_mm": 12,
        "rotation": "follow_path"
      }
    }
  ]
}
```

중요한 규칙:

- `stripe`는 motif를 직접 그리지 않는다.
- `motif`는 자신이 몇 개 배치되는지 직접 결정하지 않는다.
- 단순(circle)·복잡(bee, paisley) 도형 모두 `motif_id`로 표현한다.
- 배열 대칭(mirror/ogee/half-drop)은 motif가 아니라 Seamless/Placement가 책임진다.
- layer params의 `color`는 raw hex가 아니라 **색 슬롯 id**를 참조한다(colorway 교체용).
- `placement.host_layer`는 host geometry 계약을 통해 layer 간 관계를 표현하며, Placement는
  host 내부 구현이 아니라 그 계약에만 의존한다.
- 같은 `intent_version`·intent·seed·colorway는 같은 SVG를 생성해야 한다.

## Intent 검증·버저닝·결정론

LLM이 만든 intent를 그대로 신뢰하지 않는다. 엔진과 LLM 사이에 stage-0 검증을 둔다.

- **버저닝**: 모든 intent는 `intent_version`을 갖는다. 엔진은 버전별 해석을 고정한다.
- **구조 검증**: JSON Schema로 형태를 검증한다.
- **시맨틱 검증**:
  - `host_layer` 참조가 실제 존재하는 layer인가
  - `period_mm | tile_mm`, `lane_spacing × k = tile_period`(정수해), 요청 각도가 p/q로 스냅
    가능한가, 곡선 lane은 wavelength가 tile을 정수로 나누는가
  - 색 슬롯 참조 유효성, 각 colorway의 해석된 색 수 ≤ `production.max_colors`
  - 값 범위(음수 spacing 등)
- **복구**: 실패 시 (a) 제약을 준 re-prompt 1회 또는 (b) 안전값 클램프. 둘 다 실패하면 `422`.
  re-prompt는 authoring/검증 시점에서만 일어나며 결정론적 재현 경계 바깥이다(확정된 intent만
  결정론 대상이다).
- **결정론 계약**: 안정 정렬(`z_order` → `id`), 좌표 반올림 시점 고정(mm→px는 래스터 경계에서만),
  RNG은 seed로만 시드. candidate에 재현 메타(`intent_version`·`engine_version`·`registry_version`·
  `seed`·`colorway_id`·`layout_id`)를 기록한다.

## Placement 모델

Placement engine은 primitive instance 목록(좌표·회전·scale)을 만든다. 모든 좌표는
torus(타일 주기) 위에서 계산해 경계 연속성을 확보한다. 전략은 네 종류다.

- **lattice**: 기저벡터 2개로 정의되는 규칙 격자(정사각/직사각/마름모). 선택적 대칭 연산
  (reflect/glide)으로 mirror·half-drop·ogee 격자를 표현한다.
- **point_set**: 명시된 앵커점 집합(격자 교차점 등)에 배치한다.
- **path_following**: host가 노출한 lane 중심선(직선 또는 곡선)을 arc length로 순회하며
  `spacing_mm`/`phase_mm` 간격으로 instance를 올린다. "선 위에 motif를 올리는" 모델의 일반형이다.
- **scatter**: seed 기반 산포. blue-noise/Poisson-disk로 최소 간격을 보장하고 torus 좌표에서
  계산한다.

### 도메인 기본값: 사선

넥타이 원단이 주 사용처이므로 path_following의 **기본 방향은 사선**이다. 사용자가 명시적으로
"수직/수평 직선"을 요청한 경우에만 비사선 각도를 쓴다.

### 사선 + seamless: 각도 commensurate 스냅

직선 lane이 타일 경계에서 매끄럽게 이어지려면 lane 방향이 타일의 유리 기울기(p/q)여야 한다.
임의 각도(예: 정확히 32.0°)는 대부분 seamless가 불가능하다. 따라서 엔진은 요청 각도를
**타일과 commensurate한 가장 가까운 각도 `arctan(p/q)`로 스냅**한다. 스냅된 각도와 원 요청의
차이는 검증 단계에서 보고한다.

### 곡선 lane

곡선 lane(곡선 스트라이프, 플로럴 덩굴)은 토러스 위에서 주기적이어야 한다 — 한 변으로 나간
경로가 반대 변의 대응 좌표로 들어오고, 곡선 주기(wavelength)가 tile을 정수로 나눠야 한다.

### host geometry 계약

path_following은 host primitive의 내부 기하에 직접 접근하지 않는다. host는 다음 계약만 노출한다.

```text
HostLayer.lanes() -> [ LaneField{ id, centerline_path, spacing_mm, phase_mm, angle } ]
```

`stripe`가 이 계약을 구현해 자신의 lane 중심선을 노출하고, path_following은 stripe 내부(band
구성)가 아니라 이 계약에만 의존한다.

path_following은 두 가지 방식 중 하나로 lane을 정한다.

- **host 기반**(위 예시): `host_layer` + `lane`로 host의 `LaneField`를 고른다. `lane`은
  `LaneField.id`이거나 관용 키워드(`center` = band 중심선, `end` = band 끝선)다. 이때 placement는
  자체 path를 두지 않는다 — host 계약이 geometry의 단일 출처다.
- **자립 path**(host 없음): `path`로 lane을 직접 정의한다 — `{ kind: "straight", angle }`,
  `{ kind: "wave", wavelength, amplitude }`, `{ kind: "custom", path_id }`.

두 방식 모두 사선 commensurate 스냅과 곡선 주기 조건의 적용을 똑같이 받는다.

## SVG 출력 전략

Composition engine은 인스턴스를 일일이 나열하지 않는다. 출력 토폴로지는 고정한다.

- 타일 1개를 `<pattern>`으로 정의한다: `patternUnits="userSpaceOnUse"`, `width`/`height` =
  `tile_mm`. 기본값 `objectBoundingBox`는 commensurability·결정론을 깨므로 금지한다.
- motif geometry는 `<defs>`/`<symbol>`에 1회 정의하고 `<use>`로 인스턴싱한다. 동일 motif를 N번
  써도 정의는 1개다.
- 회전/스케일은 `patternTransform` 또는 `<use>`의 `transform`으로 적용한다(렌더러 호환성 때문에
  SVG2 전용 CSS transform은 쓰지 않는다).
- 인스턴스 enumerate(모든 도형을 펼쳐 적기)는 금지한다. 출력에 `<pattern>`이 쓰이는지 회귀 가드
  테스트로 검증한다.

## Layer 합성

Layer는 다음 정보를 가진다.

```text
id           # 필수
type         # 필수: background | stripe | motif
params       # 필수 (type별)
placement?   # motif에만. background/stripe는 host이므로 없음
z_order      # 필수
opacity?     # 기본 1.0
clip?        # 선택
```

Composition engine은 layer를 `z_order` 순서로 정렬한 뒤 하나의 SVG tile로 합성한다. SVG defs와
`<use>`는 Composition engine이 관리한다. primitive 구현이 최종 `<svg>` 문서를 직접 만들면 안 된다.

## Seamless 보장

Seamless는 각 primitive가 알아서 해결하는 문제가 아니다. 엔진 전체의 공통 책임이다.

보장 방식:

- tile 크기와 placement period의 commensurability 검증(`period | tile`, lane spacing 정수배,
  각도 p/q 스냅).
- repeat lattice = 기저벡터 2개 + 선택적 대칭 연산. 지원 대칭:
  - **block (straight)**: 단순 평행이동.
  - **half-drop / brick**: 변위 리피트. drop은 이산 enum이 아니라 `drop_fraction`(1/2·1/3·1/4)로
    일반화한다.
  - **mirror / reflect**: SVG `<pattern>`은 반사를 네이티브로 못 하므로, 엔진이 super-tile
    (2×1·2×2)에 미러 사본을 bake한 뒤 block 타일링한다. damask류 필수.
  - **glide / ogee**: 반사+변위 조합(마름모 net). 장기적으로 17 wallpaper group의 부분집합으로
    모델링한다.
  - **sateen**: N×N 격자 step-offset. 정렬선·군집을 결정론적으로 깬다(random scatter로는 보장
    불가).
- bbox가 타일 경계를 넘는 instance는 ±`tile_w`/±`tile_h`(코너 포함 최대 4사본) boundary clone을
  타일 콘텐츠에 추가한다. clone은 동일 `<symbol>`을 가리키는 `<use>` 하나일 뿐 geometry를 복제하지
  않는다(enumerate 금지 원칙 유지). `<pattern>`이 콘텐츠를 타일 박스로 클립하기 때문에 필요하다.
- scatter는 torus 좌표계(`x mod tile_w` 등 정수 격자 모듈러)에서 계산한다.

## Seamless 검증 위계

seam은 사후 측정이 아니라 by-construction으로 보장하는 것이 1차다. 검증은 3층으로 나눈다.

1. **by-construction 불변식 (1차 보증)**: commensurability·대칭 연산·boundary clone이 성립하면
   타일러빌리티는 정의상 보장된다.
2. **edge match (엄밀 판정)**: 렌더 후 반대편 변이 일치하는지(`edge_seam`) 측정한다.
3. **half-roll 가시화 (보조)**: 타일을 절반 roll해 seam을 눈으로 확인하는 디버그 보조 지표
   (`seamless_diff`). 경계 일치를 직접 측정하지는 않는다.

주의:

- edge diff = 0은 충분조건이 아니다(내부 반복 아티팩트는 별개 문제다).
- 하드엣지·안티앨리어싱·대각선이 경계를 비스듬히 지나면 edge diff가 정당하게 0이 아닐 수 있다.
- 따라서 metric은 **하드 게이트가 아니라 회귀 가드**(임계값 + per-channel 허용오차)로 쓴다.
  초과 시 "구조적 재확인" 경로로 분기한다.
- 검증 렌더러·버전·DPI를 핀으로 고정한다(rsvg-convert/resvg는 AA·서브픽셀 처리가 달라 환경
  의존적이다).

## 색·colorway 모델

- palette는 raw hex 나열이 아니라 **색 슬롯** 목록이다: `{ id, hex(preview), spot?(Pantone/TCX),
  name? }`. 슬롯의 `hex`는 **미리보기용이며 권위가 없다**. layer params는 raw hex 대신 슬롯 id를
  참조한다.
- **colorway**는 1급 개념이다: 같은 형상에 다른 색 배치를 매핑한다.
  `colorways[{ id, name, mapping(slot -> hex|spot) }]`. **출력 색은 항상 활성 colorway의 `mapping`을
  통해 해석한다**(슬롯 `hex`가 아니다). `default` colorway는 필수이며 API `colorway`가 없으면
  `default`를 쓴다. `default.mapping`은 슬롯 미리보기 hex와 일치하길 권장하나 권위는 colorway에 있다.
  같은 intent+seed+`colorway_id`는 같은 SVG를 만든다.
- 생산 제약: `canvas.dpi`(150/300/600, 기본 300), `production.{ method, max_colors }`. method가
  스크린 프린팅이면 **각 colorway의 해석된 distinct 색 수 ≤ `max_colors`**를 검증한다(색 수는
  colorway별로 센다).
- motif 색은 baked가 아니라 슬롯 참조(`currentColor`/indexed fill)로 정규화한다. 다색 motif는
  layer params의 `colors{ fill_slot -> palette_slot }`로 여러 슬롯에 바인딩한다(단색 motif는
  `color` 하나로 충분). 그래야 colorway 교체가 동작한다.
- 가멋 경고: hex가 CMYK/스팟 가멋을 벗어나면 검증 단계에서 경고한다.

## Motif 소스와 registry

motif는 단순 도형(circle 등)부터 복잡 도형(bee·flower·paisley boteh)까지 모두 포함한다. 복잡
도형은 미리 범용 라이브러리를 만들지 않고 **Recraft 같은 생성 API로 필요 시 생성**한다. 도형의
구체 모양은 매번 다르므로, 엔진은 그 내부를 모른 채 계약으로만 다룬다.

규칙:

- **authoring-time 생성·캐시**: Recraft 호출은 런타임 generate가 아니라 authoring 단계에서 한 번
  수행하고, 결과를 안정적 `motif_id`(콘텐츠 해시)로 registry에 등록한다. 런타임은 id만 참조한다.
  → 결정론(같은 intent+seed → 같은 SVG)을 유지한다.
- **intake 정규화**: 등록 시 SVG를 정규화한다 — mm 좌표계로 정규화, tight bbox·anchor 계산,
  단일 `<symbol>`로 래핑, filter/embedded raster/외부 href 제거 또는 거부, 색을 팔레트 슬롯
  참조로 치환.
- **계약 노출**: registry의 각 motif는 `{ id, symbol, bbox_mm, anchor }`를 노출한다.
  Placement/Composition은 이 계약만 사용하고 내부 path는 모른다.

## Reference Image 처리 정책

- 참조 이미지는 **의미(스타일·모티프·색) 추출**에만 쓴다. 픽셀 충실 재현은 비목표다.
- 색은 K-means/median-cut으로 8~16색 팔레트를 추출해 색 슬롯에 매핑한다.
- 모티프는 VLM으로 구조 추출하고, 벡터화가 적합한 경우에만 motif로 등록한다.
- **벡터화 적합/부적합 경계**:
  - 적합: 플랫·기하·단순 윤곽 도형(potrace/VTracer류로 깨끗한 path가 나옴).
  - 부적합: 사진·유화·유기적 질감(벡터화 시 파일 팽창 + 디테일 손실로 SVG 목적이 무력화).
- 부적합 텍스처는 (a) 명시적 비목표로 거부하거나 (b) 하이브리드 폴백(타일 단위로 사전 타일링한
  embedded raster, seam 통과 필수)으로 처리한다.
- diffusion 생성은 최종 산출이 아니라 moodboard/스타일 전처리에만 쓴다(벡터 SSOT 원칙 유지).
- API 응답에 `source_fidelity` 메타로 재현 한계를 표기한다.

## 패턴 계열 → 엔진 구성 매핑

어떤 텍스타일 계열을 어떤 엔진 구성으로 만드는지의 매핑이다. 커버리지를 명시하고 추상화 과적합을
방지한다.

| 계열 | primitive | placement | seamless 대칭 |
|---|---|---|---|
| 사선 스트라이프(repp) | stripe(직선 lane) | — | block + 각도 스냅 |
| 클럽 타이(사선 스트라이프 + 모티프) | stripe + motif | path_following(사선) | block |
| 푸라르/올오버 도트·소모티프 | motif | lattice 또는 sateen | half-drop / sateen |
| 플로럴(곡선 덩굴) | stripe(곡선 lane) + motif | path_following(곡선) + scatter | block / half-drop |
| 페이즐리(boteh) | motif | lattice + path_following | mirror / ogee |
| 다마스크 | motif | lattice | mirror(super-tile) |
| 이카트·사진적 텍스처 | — | — | 벡터 부적합 → 비목표/하이브리드 |

> 모든 계열은 background layer를 깔 수 있다(표는 stripe/motif 구성만 표기). placement `—`는 모티프
> 배치가 없다는 뜻이며 PlacementEngine 단계를 건너뛰는 것은 아니다.

## 보안 모델

LLM·사용자 입력을 받아 SVG를 emit하므로 다음을 강제한다.

- **출력 인코딩**: 직렬화 계층에서 속성값 quote-escape, 텍스트 `& < >` 이스케이프. f-string
  직접 보간 금지.
- **입력 allowlist**: hex는 `^#[0-9a-fA-F]{3,8}$`, `motif_id`는 registry 키, `href`는 내부
  `#id` fragment만 허용(외부 URL·`javascript:` 금지). 태그/속성 allowlist.
- **reference_image 업로드**: 포맷·크기·픽셀 상한, 디코드 타임아웃, 메타데이터 strip, SSRF 차단.

## 제품 API

최종 제품 표면은 하나다.

```text
POST /api/v1/generate
  body: {
    prompt: string,
    reference_image?: image,
    canvas?,
    palette?,
    colorway?,
    seed?,
    candidate_count?
  }
  -> {
    request_id,
    candidates: [
      { id, svg, intent, layout_id, source_fidelity }
    ]
  }
```

- `reference_image`가 없으면 text-to-image 경로로 처리한다.
- `reference_image`가 있으면 image-to-image 경로로 처리하되, 참조 이미지는 스타일, 모티프, 색을
  intent JSON으로 해석하는 데만 사용한다(픽셀 재현 비목표).
- LLM adapter는 intent JSON을 만들 뿐, SVG 좌표나 raw SVG를 만들지 않는다. intent는 항상
  `intent_version`을 갖고 stage-0 검증을 통과해야 한다.
- 포괄 요청은 여러 compatible layout 후보를 반환한다. 후보 다양성 축은 layout 변형·placement
  종류·colorway·seed이며, 랭킹 기준은 seam 통과·색 수·directional clustering이다. 중복 후보는
  de-dup한다. `layout_id`는 후보를 만든 배치·대칭 구성의 식별자이며 다양성·de-dup·재현 키의 일부다.
- 구체 요청은 `candidate_count=1`로 줄일 수 있다.
- 에러는 `4xx`(스키마)·`422`(시맨틱 검증 실패)·`5xx`(렌더러·LLM)로 분류하고, 일부 후보만 실패하면
  부분 성공으로 반환한다. `request_id`는 로그·메트릭에 전파한다.

## MVP 성공 기준

첫 MVP는 다음 intent를 LLM 없이 직접 넣어 생성할 수 있어야 한다(넥타이 사선 시나리오).

```text
background
+ diagonal stripe layer
+ stripe lane 위 circle motif layer (path_following)
+ stripe lane 위 bee motif layer (path_following)
-> seamless SVG
```

성공 조건:

- stripe primitive와 motif primitive가 서로 독립적으로 존재한다.
- circle/bee 같은 형상은 stripe 내부 옵션이 아니라 `path_following` placement로 올라간다.
- path_following은 stripe 내부가 아니라 host geometry 계약(`lanes()`)에만 의존한다.
- 사선 각도는 tile-commensurate 값으로 스냅된다.
- 최종 결과는 Composition engine이 `<pattern>` + `<use>` 기반 하나의 SVG로 만든다(인스턴스
  enumerate 금지).
- 같은 seed·intent·colorway는 같은 SVG를 만든다.
- by-construction 불변식과 edge match 검증을 통과한다.

추상화 과적합을 막기 위해, MVP 직후 **사선 lane이 아닌 계열 하나**(예: scatter 또는 lattice
기반 올오버 모티프)도 같은 엔진으로 만들 수 있어야 한다.
