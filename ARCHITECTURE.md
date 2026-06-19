# 아키텍처 — AI Seamless SVG 생성기

텍스트 또는 참조 이미지로부터 **이음매 없는(seamless) 텍스타일 패턴 SVG**를 생성하는
서비스다. 엔진의 핵심은 완성 패턴 클래스를 많이 만드는 것이 아니라, 다음 네 단계를 분리하는
것이다.

```text
Primitive 생성  +  Placement 계산  +  Layer 합성  +  Seamless 보장
```

LLM은 `prompt -> intent JSON`(+ 모티프 명세) 변환만 담당한다. SVG 좌표, 반복, 배치, 합성,
seamless 보장은 모두 결정론적 엔진이 담당한다.

> 이 문서는 **안정적 설계 레퍼런스**다. 코드로 읽는 게 정확한 저수준 메커니즘은 본문에
> 복제하지 않고 `경로:심볼` 포인터만 남긴다(라인 번호는 drift하므로 심볼명을 쓴다). 모티프
> 라이브러리·멀티컬러의 **결정 로그·단계별 실행 계획**은 별도 문서
> `docs/spec/motif-library-and-multicolor.md`(+ `docs/plan/*`)에 있으며, 이 문서와 통합하지 않는다.

> 도메인 전제: 주 사용처는 **넥타이 원단**이다. 패턴의 기본 방향은 **사선**이지만, 이는 LLM/intent
> authoring 관례이지 엔진 기본값이 아니다 — 엔진은 `path.angle`이 없으면 0.0(수평)으로 두고 스냅한다
> (`app/engine/placement/path_following.py:_centerline_from_path`).

## 핵심 원칙

- **Primitive: background / stripe / motif**: 완성 패턴이 아니라 재사용 가능한 SVG 조각이다.
  단, 코드상 *primitive 모듈*은 `background`·`stripe` 둘뿐이고(`app/engine/primitives/`), `motif`는
  registry 산출물로 다룬다(`app/motifs/registry.py:get_motif`, composition이 `<symbol>`/`<use>`로 인스턴싱).
  `stripe`는 band를 그리는 동시에 placement가 따라갈 lane 중심선(lane field)을 노출하는 primitive다.
- **모양과 배열의 분리**: motif는 "무엇을 그리는가"만 책임진다. mirror·half-drop·sateen 같은 *배열
  대칭*은 motif가 아니라 Seamless/Placement의 책임이다.
- **Placement는 계약에만 의존**: Placement는 구체 primitive 타입이 아니라 host가 노출하는 geometry
  계약(lane field)에만 의존한다.
- **Layer 합성 분리**: 최종 SVG는 Composition engine이 layer를 `z_order`(→ `id`) 순으로 합성한다.
- **구조적 seamless**: 픽셀 보정이 아니라 commensurability, 대칭 연산(mirror/glide super-tile),
  pattern transform, torus wrap, boundary clone으로 경계 연속성을 보장한다.
- **결정론**: 같은 `intent_version`·intent·seed·colorway는 항상 바이트 동일 SVG를 만든다. motif
  생성(LLM/Recraft)·임베딩 검색·변형 샘플링 같은 비결정 단계는 엔진 경계 밖에서 끝낸다.
- **벡터 우선**: SVG가 단일 진실 공급원이다. PNG/TIFF는 SVG를 래스터화한 파생물이다.
- **mm 기반 단위**: 기하는 내부적으로 밀리미터로 다룬다. `px = round(mm/25.4*dpi)` 변환은 래스터
  경계에서만 수행한다(`app/engine/units.py:mm_to_px`).
- **외부 의존성 격리**: LLM, 임베딩, motif 생성기(Recraft), 참조 이미지, 저장소(Supabase)는 코어
  엔진 바깥 어댑터로 둔다(`app/adapters/`, "어댑터 경계" 섹션).

## 코드 구조

```text
app/
├── main.py                      # FastAPI 앱·라우터 마운트·lifespan(store/어댑터 설치, registry hydrate)
├── core/
│   ├── config.py                # Settings + 상수(버전·DPI·캡·tau·motif 게이트). "설정·상수" 섹션
│   └── observability.py         # X-Request-ID 미들웨어, log_metrics
├── engine/
│   ├── intent.py                # 엔진 입력 계약(pydantic), intent_version, Placement/MotifParams 검증자
│   ├── units.py                 # mm/px, snap_angle/snap_spacing, SVG 숫자 포맷
│   ├── palette.py               # 색 슬롯·colorway·named preset, distinct_colors, gamut 휴리스틱
│   ├── host.py                  # HostLayer/LaneField 계약, Centerline(straight/wave), resolve_lane
│   ├── primitives/              # background, stripe(lane field 노출)
│   ├── placement/               # lattice, point_set, path_following, scatter — 모두 Instance 방출
│   ├── composition.py           # layers[] -> <pattern>/<symbol>/<use> SVG tile (멀티컬러 슬롯 바인딩)
│   ├── seamless.py              # commensurability 재단언, boundary clone, mirror/glide super-tile
│   ├── determinism.py           # 안정 정렬, layout_id, ReproMeta, select_variant/stable_hash
│   ├── generate.py              # intent -> 단일 candidate(byte-deterministic, 래스터 제외)
│   └── candidates.py            # intent -> 랭킹된 candidate set(다양성 축·rank·de-dup)
├── motifs/
│   ├── registry.py              # 인메모리 레지스트리 + normalize_motif_svg(intake/슬롯화/구조 게이트), 라이프사이클
│   ├── geometry.py              # 결정론적 bbox 계산(transform 인지, 베지어/아크 over-estimate)
│   ├── facets.py                # scope 통제 어휘, variant_group_key(versioned)
│   └── store.py                 # Supabase(psycopg) CRUD/쿼리, MotifRecord, graceful no-op
├── render/
│   ├── svg.py                   # 직렬화·escape_text/escape_attr
│   ├── raster.py                # 외부 CLI 렌더러(rsvg/resvg) + Pillow DPI 스탬프
│   └── sanitize.py              # defusedxml 파싱 + allowlist(sanitize_svg/scrub_svg)
├── adapters/
│   ├── base.py                  # AdapterResult, cache_key, 주입 Protocol seam
│   ├── gemini.py                # 채팅 LLM(Gemini)
│   ├── llm.py                   # prompt -> intent + 모티프 명세, 단색 motif 생성, 재프롬프트 1회
│   ├── embedding.py             # OpenAI text-embedding-3-small, LRU 캐시
│   ├── recraft.py               # 멀티컬러 motif 생성 + 적합성 게이트(_flatten_unsuitable)
│   ├── image.py                 # 참조 이미지: 업로드 하드닝, median-cut 팔레트, VLM/벡터화 판정
│   ├── motif_resolver.py        # 오케스트레이션 글루(조회→생성→주입, 변형 샘플링, Tier-1 cascade)
│   └── registry_fingerprint.py  # 요청 시점 registry_version 풀 지문
├── validate/
│   ├── intent.py                # stage-0 검증·복구(시맨틱 규칙 전부)
│   └── seamless.py              # tiling_seam(주 회귀 가드)·edge_seam·seamless_diff
└── api/
    ├── routes/{generate,export,palettes,health}.py
    └── schemas/generate.py      # Request/Response pydantic 모델(공개 계약)
```

## 엔진 데이터 흐름

```text
GenerateRequest
  -> IntentBuilder (어댑터: prompt/이미지 -> intent + 모티프 명세)
  -> motif_resolver (모티프 명세 -> 조회/생성 -> concrete motif_id 주입)
  -> Intent JSON (intent_version)
  -> IntentValidator (stage-0: 검증·복구)
  -> Composition (PrimitiveFactory + PlacementEngine + SeamlessEngine 통합)
  -> SVG candidate (+ repro 메타)
  -> Validation(seam)/sanitize/export 어댑터
```

엔트리포인트는 둘이다. `app/engine/generate.py:generate`는 단일 후보를 byte-deterministic하게
만든다(래스터화 제외). `app/engine/candidates.py:generate_candidates`는 다양성 축으로 후보 set을
만들고 랭킹·de-dup한다("제품 API" 참고). 각 단계 책임은 겹치지 않는다.

## Intent 계약

최종 제품 API는 SVG를 직접 요청하지 않는다. 사용자는 prompt·참조 이미지·canvas·palette·seed
정도만 보내고, 엔진이 이를 intent JSON으로 바꿔 실행한다. intent의 권위 있는 스키마는 pydantic
모델이다 — 필드·타입·검증자는 `app/engine/intent.py:Intent`(+ `Placement`/`MotifParams`/`SymmetrySpec`)에서
읽는다. 아래는 형태 감을 잡는 골격일 뿐이다.

```jsonc
{
  "intent_version": 1,
  "canvas": { "tile_mm": 48, "dpi": 300 },          // dpi ∈ {150,300,600}
  "seed": 184231,
  "production": { "method": "digital", "max_colors": 12 },  // digital | screen
  "palette": { "slots": [{ "id": "ground", "hex": "#10243a", "spot": "19-4024 TCX" }, ...] },
  "colorways": [{ "id": "default", "mapping": { "ground": "#10243a", ... } }],  // default 필수
  "symmetry": { "kind": "mirror_2x2" },             // 선택, top-level
  "layers": [
    { "id": "ground", "type": "background", "z_order": 0, "params": { "color": "ground" } },
    { "id": "stripe_base", "type": "stripe", "z_order": 1,
      "params": { "angle": -32, "period_mm": 24, "bands": [{ "offset_mm": 6, "width_mm": 12, "color": "accent" }] } },
    { "id": "bee", "type": "motif", "z_order": 2,
      "params": { "motif_id": "bee", "size_mm": 5, "color": "gold" },     // 단색: color
      "placement": { "type": "path_following", "host_layer": "stripe_base", "lane": "end",
                     "spacing_mm": 24, "phase_mm": 12, "rotation": "follow_path" } }
  ]
}
```

규칙:

- `stripe`는 motif를 직접 그리지 않고, `motif`는 자신의 배치 수를 직접 정하지 않는다.
- 단순(circle)·복잡(bee, paisley) 도형 모두 `motif_id`로 표현한다.
- 배열 대칭(mirror/half-drop/glide/sateen)은 motif가 아니라 Seamless/Placement가 책임진다.
- layer params의 색은 raw hex가 아니라 **색 슬롯 id**를 참조한다. 단색은 `color`(슬롯 1개),
  멀티컬러는 `colors{ 모티프슬롯 -> 팔레트슬롯 }`(정확히 하나만 지정 — `MotifParams` 검증자).
- `placement`는 `MotifLayer`에만 있다. `placement.host_layer`는 host의 lane 계약을 통해 layer 관계를
  표현하며, Placement는 host 내부 구현이 아니라 그 계약에만 의존한다.
- 같은 `intent_version`·intent·seed·colorway는 같은 SVG를 만든다.

## Intent 검증·결정론

LLM이 만든 intent를 그대로 신뢰하지 않는다. 엔진과 어댑터 사이에 stage-0 검증을 둔다
(`app/validate/intent.py:validate_intent`; 실패는 `IntentInvalid` → HTTP 422, 성공은 `ValidationResult`).

- **구조 검증**: JSON Schema가 아니라 **pydantic 모델**로 한다 — 모든 모델이 `extra="forbid"`라
  미지 필드를 거부한다(`Intent.model_validate`).
- **시맨틱 검증**: `host_layer` 참조 존재, 각도 p/q 스냅 가능성, lane closure `L = tile×hypot(p,q)`의
  약수 spacing, 곡선 lane wavelength가 `L`을 정수로 나눔, lattice cell이 tile을 나눔 +
  `drop_fraction ∈ {1/2,1/3,1/4}` + torus drop-closure, scatter min_dist/sateen 코프라임, 색 슬롯
  참조 유효성, 멀티컬러 `colors`가 motif `color_slots`를 정확히 덮음, screen일 때 colorway별 distinct
  색 수 ≤ `max_colors`. 규칙 전체와 헬퍼는 `validate_intent`에서 읽는다(추가될수록 본문은 drift한다).
- **복구**: (a) 어댑터의 제약 재프롬프트 1회(authoring 시점, 결정론 경계 밖 —
  `app/adapters/llm.py:build_intent`), 또는 (b) 안전값 클램프(dpi → 가장 가까운 허용값, 경고).
  둘 다 안 되면 `422`.
- **결정론 계약**: 안정 정렬(`z_order` → `id`), mm→px 반올림은 래스터 경계에서만, RNG은
  `random.Random(seed)`만 사용(전역 random 미사용). candidate에 재현 메타 `ReproMeta`를 기록한다
  (`app/engine/determinism.py:ReproMeta` — `intent_version·engine_version·registry_version·seed·colorway_id·layout_id`).
  이 중 `registry_version`은 상수가 아니라 **요청 시점에 curated 풀을 지문화**한 값이다
  (`REGISTRY_VERSION+"+pool.<hex8>"`, `app/adapters/registry_fingerprint.py:registry_version_for`).

## Placement 모델

Placement engine은 primitive instance 목록(`Instance{x_mm, y_mm, rotation_deg}`, torus 좌표)을 만든다.
모든 좌표는 타일 주기 위에서 계산해 경계 연속성을 확보한다. 전략 네 종류는 `placement.type`으로
디스패치한다(`app/engine/placement/__init__.py:place`).

- **lattice**: 기저벡터 2개로 정의되는 격자. `drop_fraction` + `drop_axis`(column=half-drop, row=brick)로
  변위 리피트를 표현한다(`app/engine/placement/lattice.py`).
- **point_set**: 명시된 앵커점 집합에 배치한다.
- **path_following**: host가 노출한 lane 중심선을 arc length로 순회하며 `spacing_mm`/`phase_mm` 간격으로
  올린다. "선 위에 motif를 올리는" 모델의 일반형이다(`app/engine/placement/path_following.py`).
- **scatter**: seed 기반 산포. `poisson`(blue-noise dart-throwing, 연속 float torus 최소거리)과 `sateen`
  (N-end step grid)을 가진다(`app/engine/placement/scatter.py`). torus wrap은 정수 격자가 아니라 mm 연속
  좌표의 최소상(min-image) 거리로 계산한다.

### 사선 + seamless: 각도/간격 commensurate 스냅

직선 lane이 타일 경계에서 매끄럽게 이어지려면 lane 방향이 타일의 유리 기울기(p/q)여야 한다. 임의
각도는 대부분 seamless가 불가능하므로, 엔진은 요청 각도를 **commensurate한 가장 가까운 `arctan(p/q)`로
스냅**한다(`app/engine/units.py:snap_angle`, 분모 상한 `MAX_LANE_PERIOD_TILES=16`).

직선 lane의 한 바퀴(closure) 길이는 한 변이 아니라 `L = tile_mm × hypot(p, q)`다. `spacing_mm`은 `tile`이
아니라 이 `L`을 나눠야 wrap 지점에서도 간격이 균일하다. 대부분의 스냅 각도는 `hypot(p,q)`가 무리수라
정확히 못 나누므로, 엔진은 요청 간격을 거부하지 않고 **가장 가까운 약수로 스냅**한다(각도 스냅과 같은
철학; `app/engine/units.py:snap_spacing`). 이미 나누어떨어지면 스냅은 무연산이라 바이트 동일성이 유지된다.
편차는 검증 단계에서 경고로 보고한다.

### 곡선 lane

곡선 lane(wave)은 토러스 위에서 주기적이어야 한다 — 곡선 wavelength가 `L`을 정수로 나눠야 한다
(축 정렬 lane에서만 `wavelength | tile`로 단순화된다). wave 기하·접선은 `app/engine/host.py:Centerline`이
analytic으로 계산하고, 주기 조건은 런타임이 아니라 `validate_intent`가 강제한다.

### host geometry 계약

path_following은 host primitive 내부 기하에 직접 접근하지 않고 `HostLayer.lanes() -> [LaneField]`
계약에만 의존한다. `LaneField`의 정확한 필드(저장 필드 + 파생 `angle_deg` property)와 `resolve_lane`
키워드 해석은 `app/engine/host.py:LaneField`/`resolve_lane`, 그리고 stripe가 노출하는 `center`/`end`/`start`
lane은 `app/engine/primitives/stripe.py:Stripe.lanes`에서 읽는다. lane은 두 방식으로 정한다 — host 기반
(`host_layer` + `lane`) 또는 자립 path(`path`, host 없음). custom path_id는 현재 범위 밖(거부).

## SVG 출력 전략

Composition engine은 인스턴스를 일일이 나열하지 않는다. 출력 토폴로지는 고정한다(`app/engine/composition.py:compose`).

- 타일 1개를 `<pattern patternUnits="userSpaceOnUse">`로 정의한다(`objectBoundingBox`는 commensurability·
  결정론을 깨므로 금지).
- motif geometry는 `<defs>`/`<symbol>`에 정의하고 `<use>`로 인스턴싱한다. 동일 motif는 `motif_id`로
  dedup해 정의 1개를 공유한다(멀티컬러는 슬롯당 symbol — "색·colorway" 참고).
- 회전/스케일은 `patternTransform`/`<use transform>`으로 적용한다(SVG2 전용 CSS transform 미사용).
- 인스턴스 enumerate 금지. 출력에 `<pattern>`이 쓰이는지 회귀 가드 테스트로 검증한다.

## Layer 합성

`Layer`는 `id·type·params·z_order·opacity?`를 갖고 `placement?`는 `MotifLayer`에만 있다. 정확한 필드는
`app/engine/intent.py:Layer`(`BackgroundLayer`/`StripeLayer`/`MotifLayer`)에서 읽는다.

Composition engine은 layer를 `z_order`(→ `id`) 안정 정렬한 뒤 하나의 SVG tile로 합성하며,
**적용하는 것은 layer 순서와 opacity(`<g opacity>`)뿐**이다. blend·per-layer clip은 구현되어 있지
않다(`clip` 필드는 모델에 선언되어 있으나 composition이 소비하지 않는 예약 필드다). defs와 `<use>`는
Composition engine이 관리한다 — primitive 구현이 최종 `<svg>` 문서를 직접 만들지 않는다.

## Seamless 보장

Seamless는 각 primitive가 아니라 엔진 전체의 공통 책임이며, **by-construction**이 1차 보증이다.
경계 연속성은 세 메커니즘으로 보장한다.

- **commensurability**: `period | tile`, lane spacing은 closure `L`의 약수로 스냅, 각도 p/q 스냅. 생성
  경계에서 재단언한다(`app/engine/seamless.py:assert_seamless_invariants` — 단, `spacing|L`은 강제하지 않음).
- **repeat lattice·대칭**: block/half-drop/brick은 `drop_fraction`+`drop_axis`로 일반화한다
  (`app/engine/placement/lattice.py`). mirror/glide는 SVG `<pattern>`이 반사를 네이티브로 못 하므로 엔진이
  super-tile에 반사 사본을 bake한 뒤 block 타일링한다 — 구현 종류는 `mirror_h`(2×1)·`mirror_v`(1×2)·
  `mirror_2x2`(2×2)·`glide_h`·`glide_v`다(`app/engine/seamless.py:super_tile`; `SymmetrySpec`는 top-level
  intent 필드). sateen은 N×N step-offset로 정렬선·군집을 결정론적으로 깬다(`scatter.py:_place_sateen`).
  ogee/전체 wallpaper group은 향후 과제다.
- **boundary clone**: bbox가 타일 경계를 넘는 instance에 ±`tile` 시프트 사본을 더한다(엣지 1~3개, 코너는
  4개 사본). clone은 동일 `<symbol>`을 가리키는 `<use>`일 뿐 geometry를 복제하지 않는다
  (`app/engine/seamless.py:clone_instances`, `size_mm ≤ tile_mm` 가정).

## Seamless 검증 위계

seam은 사후 측정이 아니라 by-construction으로 보장하는 것이 1차다. 래스터 검증은 보조다
(`app/validate/seamless.py`).

1. **by-construction 불변식 (1차 보증)**: commensurability·대칭·boundary clone이 성립하면 타일러빌리티는
   정의상 보장된다.
2. **tiling_seam (주 회귀 가드)**: 내부 seam의 인접 픽셀 불연속을 내부 baseline과 비교한 초과량.
   `TILING_SEAM_TOL = 1.0`. 모든 seamless 테스트가 이 지표로 단언한다.
3. **edge_seam / seamless_diff (보조)**: 반대편 변 차이(엄밀 판정)와 half-roll 가시화(디버그). `edge_seam`은
   모티프 intake 오버플로 게이트로도 쓰인다(`motif_edge_seam_tol=2.0`).

주의:

- 하드엣지·AA·대각선이 경계를 비스듬히 지나면 픽셀 diff가 정당하게 0이 아닐 수 있다. 그래서 metric은
  하드 게이트가 아니라 회귀 가드다.
- 래스터 렌더러는 **버전 핀이 아니다** — `find_renderer()`가 PATH에서 `rsvg-convert`(우선) 또는 `resvg`를
  런타임 선택한다(`app/render/raster.py`). 핀으로 고정되는 것은 DPI뿐이며, AA·서브픽셀 처리가 렌더러별로
  달라 환경 의존적임에 유의한다.

## 색·colorway 모델

- palette는 raw hex 나열이 아니라 **색 슬롯** 목록이다: `ColorSlot{ id, hex(preview), spot?, name? }`.
  슬롯의 `hex`는 **미리보기용이며 권위가 없다**. layer params는 raw hex 대신 슬롯 id를 참조한다.
- **colorway**는 1급 개념이다: 같은 형상에 다른 색 배치를 매핑한다(`mapping: slot -> hex|spot`). **출력 색은
  항상 활성 colorway의 mapping을 통해 해석한다**(슬롯 hex가 아니다). `default` colorway는 필수이며 API
  `colorway`가 없으면 `default`를 쓴다. 슬롯·colorway 데이터 모델과 그 불변식(default 필수, mapping이 선언
  슬롯을 정확히 덮음)은 `app/engine/palette.py:ColorSlot/Colorway/Palette`에서, 그 검증·gamut 경고·
  screen max_colors 체크는 `app/validate/intent.py:validate_intent`에서 한다.
- 생산 제약: `canvas.dpi`(150/300/600), `production.{method, max_colors}`. method가 screen이면 각 colorway의
  해석된 distinct 색 수 ≤ `max_colors`를 검증한다(digital은 무제한).
- **멀티컬러 (D15 — 색 굽기 폐기)**: `<symbol>`은 colorway-무관하게 유지하고, 색은 인스턴스(`<use color>`)
  단위로 바인딩한다. intake 정규화는 색을 `currentColor`로 뭉개지 않고 **모티프-로컬 슬롯 토큰**(`s0,s1,…`,
  문서 DFS 첫 등장 순)으로 보존한다(단색 모티프만 `currentColor`로 collapse). compose 시 슬롯마다 별도
  render symbol을 만들어(활성 슬롯 → `currentColor`, 나머지 → `none`) 슬롯 수만큼 `<use color>`를 겹친다.
  정확한 토큰화·per-slot symbol·색 양자화는 `app/motifs/registry.py:_slotize_colors/slot_render_symbols/_quantize_colors`,
  바인딩은 `app/engine/composition.py:_render_motif_layer`에서 읽는다.
- 가멋 경고: hex가 CMYK/스팟 가멋을 벗어나면(보수적 HSV 휴리스틱) 검증 단계에서 경고한다(비차단).
- **hex 형식 주의**: 색 슬롯 검증(`palette.py:_HEX`)은 `#rgb`/`#rrggbb`(3·6자리)만 받고 4·8자리(alpha)는
  거부한다 — SVG sanitize allowlist의 느슨한 `^#[0-9a-fA-F]{3,8}$`보다 엄격하다.

## Motif 소스와 registry

motif는 단순 도형(circle)부터 복잡 도형(bee·paisley)까지 포함한다. 복잡 도형은 미리 범용 라이브러리를
만들지 않고 **LLM/Recraft로 필요 시 생성**한다. 엔진은 도형 내부를 모른 채 계약으로만 다룬다.

- **authoring-time 생성·캐시**: 생성 호출은 런타임 generate가 아니라 authoring 단계에서 한 번 하고, 결과를
  안정적 `motif_id`(콘텐츠 해시)로 registry에 등록한다. 런타임은 id만 참조한다 → 결정론 유지. 콘텐츠 해시는
  **정규화·슬롯화된 기하** 기준이라 colorway와 무관하다("같은 그림 → 같은 id").
- **intake 정규화** (`app/motifs/registry.py:normalize_motif_svg`): 기하를 **무차원 unit box**(extent 1.0,
  원점 중심)로 정규화하고(mm 스케일 `size_mm`은 composition에서 적용), tight bbox·anchor 계산, 단일
  `<symbol>` 래핑, 색을 모티프-로컬 슬롯 토큰으로 치환한다. filter/embedded raster/외부 href는 sanitize
  allowlist가 **거부(raise)**한다(제거가 아니라 거부).
- **구조 intake 게이트**(spec §8): drawable 존재·zero-extent·degenerate axis·aspect-ratio(`motif_max_aspect_ratio`)
  순수 기하 검사 + 렌더 기반 edge_seam 오버플로 게이트(렌더러 없으면 no-op). 임계값은 `app/core/config.py`.
- **계약 노출**: registry의 각 motif는 `{ id, symbol, bbox_mm, anchor, color_slots }`를 노출한다.
  Placement/Composition은 이 계약만 쓰고 내부 path는 모른다.

## Motif 해석·오케스트레이션

`prompt` 요청에서 모티프를 명세→획득→intent에 주입하는 서비스의 핵심 글루다
(`app/adapters/motif_resolver.py:_resolve_one`). 성격은 RAG가 아니라 **시맨틱 캐시 + 미스 시 생성**이다.

조회 순서(콜드스타트/비용 절감 위해 단계적):

1. **정확매칭**: 정규화 descriptor 5-tuple(`subject·scope·view·expression·style`)이 완전 일치하면 임베딩
   없이 hit(후보는 id 순이라 안정).
2. **하드 필터 = `scope`만**: 통제 어휘 `whole|partial`로 후보를 좁힌다(`store.find_by_facets(scope)`).
   `subject`는 자유 텍스트라 하드 필터가 아니다 — granularity(전체↔부분) 오매칭만 여기서 막는다.
3. **소프트 유사도**: descriptor 임베딩 코사인 최근접이 `tau` 이상이면 재사용(`motif_similarity_tau=0.60`).
   임베딩 텍스트는 `scope`를 제외한다(scope는 의미 토큰이 아니라 가드레일). 임베딩 미설정/실패는 fail-soft —
   최저 id 후보로 degrade.
4. **miss → 생성**: 생성 후 `normalize_motif_svg`(Tier-1 게이트) → `register_motif` → DB 영속화(`status='auto'`).

hit/생성된 모티프는 **변형 풀**을 거친다: `variant_group`의 curated 변형 중 seed로 1개 선택
(`determinism.select_variant`, 순수 함수 — 랜덤 금지). `variant_group` 키는 `sha256(version, norm(subject),
norm(scope))`로 결정론적이다(`app/motifs/facets.py:variant_group_key`, `VARIANT_GROUP_VERSION=2`).

- **생성 소스 라우팅 (D11)**: 명시 `source` 오버라이드 우선 → `complexity=='detailed'`면 Recraft(멀티컬러)
  → 그 외 LLM(단색). `app/adapters/motif_resolver.py:_route_source`.
- **Tier-1 부분 성공 cascade**: 모티프별 생성/게이트 실패 시 — 전부 실패면 502, 아니면 해당 motif layer를
  드롭하고(드롭된 host에 의존하는 layer도 cascade 드롭) 생존 intent를 200으로 반환하며 layer 순서대로
  드롭 경고를 남긴다.
- **재현**: 엔진은 concrete `motif_id`가 박힌 intent만 받으므로 비결정 단계(LLM/Recraft/임베딩/샘플링)는
  엔진 경계 밖에서 끝난다. 일회성 요청의 정식 재현 단위는 resolved-intent 스냅샷(`CandidateResponse.intent`)
  이고, 가변 전역인 풀 변화는 `registry_version` 풀 지문으로 봉인된다.

## 어댑터 경계

LLM·임베딩·생성기·참조 이미지·저장소는 코어 바깥 어댑터로 격리한다. 공통 seam은 **주입 Protocol +
지연 SDK import + opt-in 네트워크 + freeze 캐시**다 — 클라이언트는 `app/main.py` lifespan이 키가 있을 때만
설치하고, 미설정이면 graceful degrade한다. SDK/네트워크 실패는 `AdapterClientError`로 정규화(→ 502).

| 어댑터 | 역할 | 모델/주의 |
|---|---|---|
| `gemini.py` | 채팅 LLM(intent + 모티프 명세) | `gemini-2.5-flash-lite`, temp 0.0 |
| `embedding.py` | descriptor 임베딩 | OpenAI `text-embedding-3-small`(채팅과 별개, D12), LRU 512 |
| `recraft.py` | 멀티컬러 motif 생성 | `recraftv4_1_vector`; 적합성 게이트 `_flatten_unsuitable`(gradient/filter→평탄화, rgb()→#hex, 풀캔버스 배경 제거, raster 거부), 색 ≤ `recraft_max_color_slots` |
| `image.py` | 참조 이미지 처리 | 업로드 하드닝 → median-cut 팔레트 → 벡터화 판정 |
| `llm.py` | prompt→intent, 단색 motif 생성 | 명세 facet 검증, 재프롬프트 1회 |
| `base.py` | `AdapterResult` 봉투, `cache_key` | canonical JSON 해시(= `layout_id_for` 직렬화) |

결정론은 어댑터 freeze 캐시가 받친다(같은 prompt/canonical-spec → 같은 산출 → 같은 콘텐츠 해시 id).
구체 캐시 키·재시도는 각 어댑터 코드에서 읽는다.

## 영속화 — Supabase 공유 DB & 마이그레이션 소유권

이 서비스는 독립 DB를 두지 않고 **제품 전체가 공유하는 단일 Supabase 프로젝트**에 붙는다. React
모노레포(`YeongSeon`)와 이 Python 서비스가 같은 Postgres를 가리킨다.

- **스키마·마이그레이션 소유자는 React 모노레포 하나뿐이다.** Supabase는 적용 마이그레이션을 DB 안 단일
  원장(`supabase_migrations.schema_migrations`)에 기록하므로, 두 레포가 각자 push하면 원장이 갈라진다
  (divergence). 한쪽의 `db reset`이 다른 쪽 스키마까지 날린다. 스키마 SSOT는 모노레포의 선언적 스키마
  (`supabase/schemas/*.sql`)이고, 마이그레이션도 거기서 `pnpm db:new`로만 만든다. `motifs` 테이블도 그 소유
  아래에 있다.
- **이 서비스는 DB 클라이언트로만 붙는다.** `app/motifs/store.py`가 `SUPABASE_DB_URL`(direct Postgres DSN,
  psycopg)로 `motifs` 테이블만 읽고/쓴다. 이 레포는 `supabase/` 폴더도, 마이그레이션도, `db push`도 두지
  않는다. 앱 런타임은 DDL을 실행하지 않는다.
- **DSN은 서버 사이드 전용**: direct connection은 PostgREST RLS를 우회하므로 클라이언트 노출 금지.
- **미설정 시 폴백**: `SUPABASE_DB_URL`이 비면 store는 `None`이고 registry 경로는 no-op(에러 아님) —
  in-memory registry로만 동작한다. 명시 호출자(`_resolve_store`)만 `MotifStoreNotConfigured`(502급)를 던진다.

`motifs` 스키마의 권위 있는 DDL은 **모노레포**가 소유한다. 이 서비스가 의존하는 컬럼 집합과 형태는 코드에서
읽는다 — `app/motifs/store.py`의 `_COLUMNS`/`MotifRecord`/`_row_to_record`. 클라이언트가 의존하는 load-bearing
계약만 박제하면:

- `id`는 content-hash **PK**, INSERT는 `ON CONFLICT (id) DO NOTHING`로 멱등.
- 통제 facet 컬럼은 `scope`(`whole|partial`)다 — 과거 `part`에서 rename됨(commit `2fd5e17`).
- `color_slots`/`bbox`/`anchor`는 `jsonb`, `tags`는 native `text[]`(jsonb 아님), `embedding`은 pgvector.
- `source ∈ {builtin, llm, recraft}`(기본 `recraft`), `status ∈ {auto, curated}`(기본 `auto`).
- 부팅 시 `hydrate_from_store`로 일괄 복원 + 콜드 미스 시 lazy 단건 로드(`app/main.py` lifespan,
  `registry.py:get_motif`).

> **ivfflat(vector) 인덱스 — 모노레포 핸드오프.** 카탈로그가 작은 동안은 두지 않고 seq scan으로 충분하다.
> 행 수가 충분해지면 모노레포가 `supabase/schemas/`에 추가한다(이 레포는 DDL 미실행). 현재 리졸버는
> `scope` 하드필터 + Python-side 코사인 유사도를 쓰므로(`app/adapters/motif_resolver.py:_best_by_similarity`)
> pgvector `ORDER BY embedding` 쿼리가 없어 인덱스는 현재 질의 경로에서 쓰이지 않는다 — 가치는 향후
> DB-side 벡터 검색 도입 시 실현된다. ivfflat은 근사라 recall 회귀 가능성이 있으니 도입 시 exact 대비
> 동등성을 검증한다.

## Reference Image 처리 정책

- 참조 이미지는 **의미(스타일·모티프·색) 추출**에만 쓴다. 픽셀 충실 재현은 비목표다.
- 색은 **Pillow median-cut**으로 2~16색 팔레트를 추출해 색 슬롯에 매핑한다(K-means/scikit-learn 미사용,
  의존성 free, 결정론적; `app/adapters/image.py:extract_palette`).
- 모티프는 VLM으로 구조 추출하고, 벡터화 적합 판정을 거친다 — `judge_vectorization`이 path·color 수
  임계(`VECTORIZE_MAX_PATHS=1500`, `VECTORIZE_MAX_COLORS=32`)로 적합(`vector`)/부적합(`raster_hybrid`)을
  가른다(픽셀이 아니라 카운트만 본다).
- 부적합 텍스처는 현재 팔레트 + 라이브러리 모티프 폴백 + 경고로 처리하고, raster-hybrid 베이킹은 향후 과제다.
- 업로드 하드닝(인코딩/디코드 크기 캡, 포맷 allowlist PNG/JPEG/WEBP, per-side·픽셀 상한, verify 무결성,
  메타데이터 strip)이 팔레트 추출 전에 돈다. 위반은 `IntentInvalid`(→ 422). `app/adapters/image.py`.
- diffusion 생성은 최종 산출이 아니라 moodboard/스타일 전처리에만 쓴다(벡터 SSOT 원칙 유지).
- API 응답에 `source_fidelity` 메타로 재현 한계를 표기한다.

## 패턴 계열 → 엔진 구성 매핑

어떤 텍스타일 계열을 어떤 엔진 구성으로 만드는지의 (목표) 매핑이다. 커버리지를 명시하고 추상화 과적합을
방지한다.

| 계열 | primitive | placement | seamless 대칭 |
|---|---|---|---|
| 사선 스트라이프(repp) | stripe(직선 lane) | — | block + 각도 스냅 |
| 클럽 타이(사선 스트라이프 + 모티프) | stripe + motif | path_following(사선) | block |
| 푸라르/올오버 도트·소모티프 | motif | lattice 또는 sateen | half-drop / sateen |
| 플로럴(곡선 덩굴) | stripe(곡선 lane) + motif | path_following(곡선) + scatter | block / half-drop |
| 페이즐리(boteh) | motif | lattice + path_following | mirror / (ogee=향후) |
| 다마스크 | motif | lattice | mirror(super-tile) |
| 이카트·사진적 텍스처 | — | — | 벡터 부적합 → 비목표/하이브리드 |

> 모든 계열은 background layer를 깔 수 있다(표는 stripe/motif 구성만 표기). placement `—`는 모티프
> 배치가 없다는 뜻이다.

## 보안 모델

LLM·사용자 입력을 받아 SVG를 emit하므로 다음을 강제한다(`app/render/sanitize.py`, `app/render/svg.py`,
`app/adapters/image.py`).

- **출력 인코딩**: 직렬화 계층에서 속성값 quote-escape, 텍스트 `& < >` 이스케이프, f-string 직접 보간 금지
  (`svg.py:escape_text/escape_attr`).
- **입력 allowlist**: 태그/속성 allowlist, hex 정규식, `href`는 내부 `#id` fragment만 허용(외부 URL·
  `javascript:` 금지). 파서는 **defusedxml**이라 DTD/외부 엔티티(XXE·billion-laughs)를 차단하고, 같은 local
  name을 공유하는 모호한 네임스페이스 속성은 fail-closed로 거부한다. 엔진 출력은 byte-stable 게이트
  `sanitize_svg`(통과 시 입력 그대로 — 결정론 보존), export의 신뢰 불가 입력은 재직렬화 게이트 `scrub_svg`를 쓴다.
- **reference_image 업로드**: 포맷·크기·픽셀 상한, verify 무결성, 메타데이터 strip(`image.py`). 직접 업로드라
  URL fetch가 없어 SSRF는 해당 없음.

## 제품 API

공개 계약의 권위는 pydantic 모델이다 — `app/api/schemas/generate.py`. 라우트는 `/api/v1` 아래 넷이다
(`app/api/routes/`): `POST /generate`, `POST /export`, `GET /palettes`, `GET /health`.

```text
POST /api/v1/generate
  body: { prompt?, reference_image?, canvas?, palette?, intent?, colorway?, seed?, candidate_count? }
        # 입력 우선순위 intent > reference_image > prompt; 셋 다 없으면 422.
        # candidate_count: 1..8 (기본 4)
  -> { request_id, warnings[], candidates: [ { id, svg, intent, layout_id, source_fidelity, repro } ] }
        # repro = { engine_version, registry_version, intent_version, colorway_id, seed, layout_id }
```

- `intent`를 직접 주면 그것이 권위이고 prompt/reference_image/canvas/palette는 무시(+경고)한다. 없으면
  `reference_image` → 없으면 `prompt` 경로로 처리한다. LLM adapter는 intent JSON(+ 모티프 명세)만 만들 뿐
  SVG를 만들지 않는다.
- 포괄 요청은 여러 compatible 후보를 반환한다. 다양성 축은 **layout(placement+symmetry)·colorway·seed**
  셋이다(seed는 scatter layer가 있고 layout×colorway가 요청 수를 못 채울 때만 확장). 랭킹은 `rank_key =
  (색 수, clustering, layout_id, colorway_id, seed)` 오름차순이다 — seam 통과는 랭킹 항이 아니라 그 이전의
  하드 by-construction 드롭이다. 중복은 **동일 SVG 문자열**로 de-dup한다. `layout_id`는 배치·대칭 구성의
  식별자이며 다양성·de-dup·재현 키의 일부다(`app/engine/candidates.py:generate_candidates`).
- 구체 요청은 `candidate_count=1`로 줄일 수 있다.
- **에러 분류**: 스키마(pydantic) 실패 → `400`; 시맨틱 검증 실패 → `422`; 업스트림 어댑터(LLM/Recraft/임베딩/
  렌더러) → `502`; 후보를 하나도 합성 못 하면 → `500`. 후보 생성 중 개별 layout 변형이 실패하면 드롭하고
  warning을 남긴다(전부 실패가 아닌 한 200). 모든 에러 응답은 `{detail, request_id}` + `X-Request-ID` 헤더다.
- `POST /export`: candidate SVG → PNG/TIFF 래스터(`scrub_svg` 후 외부 렌더러, dpi·mm·px 가드, `RasterError`→502,
  바이너리 응답). `GET /palettes`: named preset 팔레트. 상세는 `app/api/routes/export.py`/`palettes.py`.

## 설정·상수

시스템을 묶는 상수·임계값은 한 곳에 모은다 — `app/core/config.py`(`Settings` + 모듈 상수). 본문에 magic
number를 흩지 않는다.

- 버전: `ENGINE_VERSION`·`REGISTRY_VERSION`(`0.1.0`; `REGISTRY_VERSION`은 repro 포맷/스키마 변경 시에만 수동
  bump, 풀 변화는 요청 시점 지문이 봉인).
- 래스터 캡: `DEFAULT_DPI=300`, `ALLOWED_DPI=(150,300,600)`, `max_dpi=1200`, `max_tile_mm=2000`,
  `MAX_DIMENSION_PX=20000`, `renderer_bin`(None=자동 탐지).
- 모티프: `motif_similarity_tau=0.60`, `recraft_max_color_slots=6`, `motif_max_aspect_ratio=20`,
  `motif_edge_seam_tol=2.0`, `motif_render_check`(렌더 의존 검사 마스터 스위치).
- 키: `supabase_db_url`·`gemini_*`·`openai_*`·`recraft_*`(전부 서버 사이드 env).

**관측성**: 요청마다 `X-Request-ID`(없으면 생성)를 contextvar로 흘리고, `log_metrics`가 generate/export당
key=value 한 줄을 stdlib 로그로 남긴다(외부 백엔드 없음). `app/core/observability.py`.

## MVP 성공 기준

첫 MVP는 다음 intent를 LLM 없이 직접 넣어 생성할 수 있어야 한다(넥타이 사선 시나리오).

```text
background + diagonal stripe + stripe lane 위 circle motif(path_following) + bee motif(path_following)
  -> seamless SVG
```

성공 조건:

- stripe primitive와 motif가 서로 독립으로 존재한다.
- circle/bee는 stripe 내부 옵션이 아니라 `path_following` placement로 올라간다.
- path_following은 stripe 내부가 아니라 host geometry 계약(`lanes()`)에만 의존한다.
- 사선 각도는 tile-commensurate 값으로 스냅된다.
- 최종 결과는 Composition engine이 `<pattern>` + `<use>` 기반 하나의 SVG로 만든다(enumerate 금지).
- 같은 seed·intent·colorway는 같은 SVG를 만든다.
- by-construction 불변식과 `tiling_seam` 회귀 가드를 통과한다.

추상화 과적합을 막기 위해, MVP 직후 **사선 lane이 아닌 계열 하나**(scatter 또는 lattice 기반 올오버
모티프)도 같은 엔진으로 만들 수 있어야 한다.
