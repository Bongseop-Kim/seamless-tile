# seamless tile

**AI seamless SVG 생성** 서비스. 텍스트 또는 참조 이미지로부터
**이음매 없는(seamless) 텍스타일 패턴 SVG**를 생성한다. LLM은 "의도"(무엇을, 몇 개, 어떤 색)만
정하고, 좌표 배치와 seamless 보장 같은 정밀 작업은 결정론적 엔진이 책임진다. FastAPI 기반,
벡터(SVG)가 단일 진실 공급원이며 래스터(PNG/TIFF)는 SVG에서 파생한다.

설계 상세는 [ARCHITECTURE.md](ARCHITECTURE.md) 참고.

## 동작 방식

```
입력(텍스트 / 텍스트+이미지) → LLM → 의도 JSON → 결정론적 엔진(배치 + wrap) → 검증 루프 → seamless SVG
```

- **LLM은 의도 JSON만 출력**한다(좌표 없음). 싼 모델로도 고품질, 출력이 짧아 비용이 낮다.
- **seamless 100%는 엔진이 수학적으로 보장**한다(LLM 아님). 경계를 넘는 오브젝트는 반대편에
  복제(torus wrap)하고, viewBox·팔레트는 고정된다.
- **검증 루프**: 렌더 → 2x2 타일링으로 경계 확인 → 필요 시 멀티모달로 LLM에 피드백해 의도 수정.

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 래스터(PNG/TIFF) 출력에 필요한 SVG 렌더러
brew install librsvg
```

`.env.example`를 복사해 `.env`를 만들고 값을 채운다. SVG 생성만 쓸 경우 렌더러는 없어도 된다.
래스터러는 `rsvg-convert`(librsvg)를 우선 사용하고, 없으면 `resvg`로 폴백한다(`renderer_bin`으로 강제 지정 가능).

## 실행

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- 문서(Swagger): `http://localhost:8000/docs`
- 헬스: `http://localhost:8000/api/v1/health`

## 제품 API

**실제 제품 API는 단 하나다.**

```
POST /api/v1/generate
  body: {
    "prompt": "남색 바탕에 작은 코랄색 꽃을 흩뿌린 패턴",
    "reference_image": "<base64 또는 URL>",   # 선택. 있으면 image-to-image
    "canvas": { ... }, "palette": [ ... ]       # 선택 힌트
  }
  -> { "id": "...", "svg": "...", "intent": { ... } }
```

- `reference_image`가 **없으면 text → SVG**, **있으면 image → SVG**로 동작한다. 두 경로는
  LLM 입력만 다르고 같은 **의도 JSON**으로 수렴하므로 엔진·검증 루프를 100% 공유한다.
- 참조 이미지는 스타일/모티프/색을 *해석*해 의도 JSON을 채우는 데만 쓰이고, 좌표·seamless는
  여전히 엔진이 보장한다.

> **구현 현황**: `generate` API는 목표 제품 표면이며, 그 아래의 결정론적 엔진·래스터·seamless
> 측정은 완성돼 있다. LLM 어댑터·의도 JSON 스키마·검증 루프는 구축 예정이다(ARCHITECTURE.md
> "신규 레이어" 참고).

## 개발 확인용 엔드포인트

아래 패턴 엔드포인트는 **단계별 개발 확인용 스캐폴딩**이다. 엔진·래스터·seamless를 개별
검증하기 위해 의도 JSON을 거치지 않고 정밀 파라미터를 직접 받는다. 최종 제품 표면이 아니다.

```
POST /api/v1/patterns/stripe        -> { "id": "...", "svg": "..." }
POST /api/v1/patterns/stripe-dot
POST /api/v1/patterns/check
POST /api/v1/patterns/dot
POST /api/v1/patterns/herringbone

GET  /api/v1/patterns/{id}                                # 벡터(SVG)
GET  /api/v1/patterns/{id}/export?format=svg
GET  /api/v1/patterns/{id}/export?format=png&dpi=300&width_mm=200   # 인쇄용
GET  /api/v1/patterns/{id}/export?format=tiff&dpi=300&width_mm=200

POST /api/v1/patterns/{id}/colorway   # 기하 그대로 재배색 -> 새 id
GET  /api/v1/palettes                 # 명명 팔레트 목록
```

재배색은 `colors`(hex 배열) 또는 `palette`(이름) 중 **하나만** 전달한다. 원본은 보존되고 새 패턴
id가 반환된다.

```bash
curl -X POST localhost:8000/api/v1/patterns/{id}/colorway \
  -H 'content-type: application/json' -d '{"palette":"earth"}'
```

DPI/크기 협상: `dpi`는 `max_dpi`(기본 1200), `width_mm`은 `max_tile_mm`(기본 2000mm)로 제한하고
픽셀 예산을 초과하면 422를 반환한다(거대 래스터 방지). 대부분의 문서는 정사각형으로 렌더되며,
대각 `stripe-dot`처럼 자연 반복 폭/높이가 다른 패턴은 해당 비율을 보존한다.

요청 예시:

### 대각 스트라이프

`/patterns/stripe`는 항상 대각선 스트라이프를 만든다. `angle` 기본값은 `-45`이고, `0`, `90`,
`180`처럼 가로/세로 축에 정렬되는 각도는 422로 거부한다.

기본 대각 스트라이프:

```bash
curl -X POST localhost:8000/api/v1/patterns/stripe -H 'content-type: application/json' \
  -d '{"widths_mm":[10,10],"colors":["#ffffff","#00aa33"],"tile_mm":20,"angle":-45}'
```

넓은 바탕 밴드와 가는 포인트 밴드를 반복:

```bash
curl -X POST localhost:8000/api/v1/patterns/stripe -H 'content-type: application/json' \
  -d '{"tile_mm":40,"angle":-45,"background_color":"#f6f2e8","stripes":[{"offset_mm":4,"width_mm":18,"color":"#1f3a5f"},{"offset_mm":26,"width_mm":3,"color":"#b23a48"},{"offset_mm":32,"width_mm":1,"color":"#1f3a5f"}]}'
```

두꺼운 밴드, 점선 edge line, 가는 중심선 조합:

```bash
curl -X POST localhost:8000/api/v1/patterns/stripe -H 'content-type: application/json' \
  -d '{"tile_mm":48,"angle":-32,"background_color":"#10243a","stripes":[{"offset_mm":6,"width_mm":18,"color":"#0a1a2b","edge_lines":[{"position":"start","width_mm":0.8,"color":"#e02b22","style":"dotted","dot_length_mm":1.2,"gap_mm":1.2,"dot_shape":"circle"},{"position":"center","width_mm":0.4,"color":"#f0f2ee","style":"solid"}]},{"offset_mm":30,"width_mm":6,"color":"#526a89","opacity":0.65}]}'
```

핀스트라이프 느낌의 얇은 선 조합:

```bash
curl -X POST localhost:8000/api/v1/patterns/stripe -H 'content-type: application/json' \
  -d '{"tile_mm":24,"angle":-38,"background_color":"#202225","stripes":[{"offset_mm":4,"width_mm":0.6,"color":"#d7d2c4"},{"offset_mm":8,"width_mm":1.2,"color":"#7aa0c4"},{"offset_mm":14,"width_mm":0.4,"color":"#c94f4f"},{"offset_mm":19,"width_mm":0.8,"color":"#d7d2c4"}]}'
```

### 도트

기본 폴카 도트:

```bash
curl -X POST localhost:8000/api/v1/patterns/dot -H 'content-type: application/json' \
  -d '{"radius_mm":3,"spacing_mm":12,"colors":["#1a1a1a","#ffffff"],"repeat":"half_drop"}'
```

작고 촘촘한 원형 도트:

```bash
curl -X POST localhost:8000/api/v1/patterns/dot -H 'content-type: application/json' \
  -d '{"tile_mm":24,"background_color":"#fbf7ef","layers":[{"shape":"circle","size_mm":1.2,"color":"#22314d","spacing_x_mm":4,"spacing_y_mm":4}]}'
```

큰 원형 도트와 작은 다이아몬드 도트 조합:

```bash
curl -X POST localhost:8000/api/v1/patterns/dot -H 'content-type: application/json' \
  -d '{"tile_mm":48,"background_color":"#f7f3eb","layers":[{"shape":"circle","size_mm":4,"color":"#16233f","spacing_x_mm":12,"spacing_y_mm":12,"repeat":"half_drop"},{"shape":"diamond","size_mm":2,"color":"#b23a48","spacing_x_mm":24,"spacing_y_mm":24,"offset_x_mm":6,"offset_y_mm":6}]}'
```

물방울 도트 레이어:

```bash
curl -X POST localhost:8000/api/v1/patterns/dot -H 'content-type: application/json' \
  -d '{"tile_mm":48,"background_color":"#eef4ef","layers":[{"shape":"teardrop","size_mm":3,"color":"#277a6f","spacing_x_mm":16,"spacing_y_mm":16,"offset_x_mm":8,"offset_y_mm":8},{"shape":"circle","size_mm":1.5,"color":"#16233f","spacing_x_mm":8,"spacing_y_mm":8}]}'
```

### 스트라이프 + 도트

대각 스트라이프 위에 작은 도트 레이어를 올린 복합 패턴:

```bash
curl -X POST localhost:8000/api/v1/patterns/stripe-dot -H 'content-type: application/json' \
  -d '{"tile_mm":48,"angle":-32,"background_color":"#10243a","stripes":[{"offset_mm":8,"width_mm":14,"color":"#0a1a2b","edge_lines":[{"position":"start","width_mm":0.7,"color":"#e02b22","style":"dotted","dot_length_mm":1.2,"gap_mm":1.2},{"position":"end","width_mm":0.4,"color":"#f0f2ee","style":"solid"}]}],"dot_layers":[{"radius_mm":0.5,"color":"#33506c","spacing_x_mm":8,"spacing_y_mm":8}]}'
```

공통 규칙:

- 단위는 **mm**. 래스터화 시 `px = round(mm / 25.4 × dpi)`로 변환하고 DPI를 파일에 기록한다(인쇄 300, 웹 72).
- `tile_mm`은 줄 주기(스트라이프/체크) 또는 `pitch_mm`(헤링본)의 정수배여야 한다.
- 조합형 스트라이프의 점선 pitch(`dot_length_mm + gap_mm`)와 레이어드 도트의 각 간격은 `tile_mm`의 정수 약수여야 한다.
- 도트 레이어의 `shape`는 `circle`, `square`, `diamond`, `teardrop`을 지원한다.
- 색상은 `#rgb` / `#rrggbb` hex.

## 엔진 확인용 스크립트

`generate` API가 사용할 결정론적 엔진(배치 + 경계 wrap)을 직접 확인하기 위한 스크립트다.
산포(scatter)형 seamless가 어떻게 보장되는지 눈으로 검증한다.

### 복잡한 SVG seamless 래핑

외부에서 만든 복잡한 SVG도 `viewBox` 크기만큼 3x3으로 복제하고 원래 타일 영역으로 clip 하면
경계를 넘어간 도형이 반대편에서 이어지는지 확인할 수 있다. 배경은 한 번만 깔고 실제 motif만
반복 복제한다.

펠리컨 자전거 SVG 예시:

```bash
.venv/bin/python scripts/periodic_svg_wrap.py \
  ~/Downloads/a-white-pelican-riding-a-bicycle--side-view--flat-.svg \
  examples/pelican-periodic.svg

.venv/bin/python scripts/periodic_svg_wrap.py \
  ~/Downloads/a-white-pelican-riding-a-bicycle--side-view--flat-.svg \
  examples/pelican-periodic-shifted.svg \
  --shift-x 320 --shift-y 260
```

결과 확인:

```bash
open examples/pelican-periodic-shifted.svg
```

`pelican-periodic.svg`는 원본 위치 그대로 감싼 버전이고, `pelican-periodic-shifted.svg`는 motif를
일부러 오른쪽/아래로 밀어서 경계에서 잘린 부분이 반대편에 나타나는지 보는 데모다. SVG 렌더러가
있으면 PNG로도 확인할 수 있다.

```bash
rsvg-convert examples/pelican-periodic-shifted.svg -o /private/tmp/pelican-periodic-shifted.png
open /private/tmp/pelican-periodic-shifted.png
```

이 방식은 path, rect, circle 같은 순수 벡터 도형에는 구조적으로 잘 맞는다. `filter`, `blur`,
`mask`, 외부 `image`가 있는 SVG는 경계 밖으로 퍼지는 픽셀이나 외부 리소스 자체도 함께 고려해야 한다.

### 꽃 산포형 seamless

펠리컨 예시는 SVG 전체를 하나의 motif로 보고 반복한 데모다. 산포형 패턴은 보통 객체마다 좌표를
따로 잡고, 각 객체의 bbox가 타일 경계를 넘는 경우에만 반대편 복제본을 추가한다.

```txt
if x - radius < 0: draw copy at x + tile_width
if x + radius > tile_width: draw copy at x - tile_width
if y - radius < 0: draw copy at y + tile_height
if y + radius > tile_height: draw copy at y - tile_height
```

꽃/잎 객체를 흩뿌린 예시 생성:

```bash
.venv/bin/python scripts/generate_flower_scatter.py
```

결과 확인:

```bash
open examples/flower-scatter-seamless.svg
open examples/flower-scatter-repeat-preview.svg
```

`flower-scatter-seamless.svg`는 실제 1024x1024 타일이고, `flower-scatter-repeat-preview.svg`는 같은
타일을 2x2로 반복한 확인용 문서다. preview의 가운데 연한 십자선은 seam이 아니라 타일 경계를
보여주기 위한 가이드다.

PNG 렌더링 확인:

```bash
rsvg-convert examples/flower-scatter-seamless.svg -o /private/tmp/flower-scatter-seamless.png
rsvg-convert examples/flower-scatter-repeat-preview.svg -o /private/tmp/flower-scatter-repeat-preview.png
open /private/tmp/flower-scatter-repeat-preview.png
```

### Stripe + dot + motif

대각 스트라이프 위에 빨간 점선 edge line과 금색 벌 motif를 올린 예시다. 남색 직물 바탕,
대각 밴드, 점선, 벌 motif를 모두 같은 대각 주기 좌표계에 맞춰 생성한다.

```bash
.venv/bin/python scripts/generate_stripe_dot_bee.py
```

결과 확인:

```bash
open examples/stripe-dot-bee-seamless.svg
open examples/stripe-dot-bee-repeat-preview.svg
```

`stripe-dot-bee-seamless.svg`는 실제 1024x1024 타일이고,
`stripe-dot-bee-repeat-preview.svg`는 2x2 반복 확인용 문서다. preview의 가운데 연한 십자선은
seam이 아니라 타일 경계 가이드다.

PNG 렌더링 확인:

```bash
rsvg-convert examples/stripe-dot-bee-seamless.svg -o /private/tmp/stripe-dot-bee-seamless.png
rsvg-convert examples/stripe-dot-bee-repeat-preview.svg -o /private/tmp/stripe-dot-bee-repeat-preview.png
open /private/tmp/stripe-dot-bee-repeat-preview.png
```

## 테스트

```bash
.venv/bin/python -m pytest
```

래스터 테스트는 SVG 렌더러가 없으면 자동으로 건너뛴다(skip).

## 알려진 한계

- **저장소**: 패턴은 인메모리 저장이라 재시작 시 사라지고 멀티워커 간 공유되지 않는다.
- **미구현**: `generate` API의 LLM 어댑터·의도 JSON·검증 루프는 구축 예정이다. 현재는 결정론적
  엔진과 seamless 측정까지 완성된 상태다.
