# Seamless Textile Pattern Generator

되고시스템(Duegosystem) 원단 텍스타일 패턴 생성 API. 스트라이프·체크(깅엄)·도트·헤링본을
**seamless(이음매 없는 리피트) SVG**로 생성하고, 직물 질감(텍스처)을 입혀 인쇄용·웹용으로
출력한다. FastAPI 기반, 벡터(SVG)가 단일 진실 공급원이며 래스터(PNG/TIFF)는 SVG에서 파생한다.

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

## API

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

## 구조

```
app/
├── main.py                 # FastAPI 앱 (create_app)
├── core/config.py          # 환경설정 (pydantic-settings), 렌더러/DPI 설정
├── api/
│   ├── routes/             # health, patterns, export, palettes
│   ├── schemas/            # 패턴별 요청 검증 (Pydantic) + colorway
│   └── deps.py             # 인메모리 패턴 저장소
├── domain/                 # 프레임워크 비의존 코어
│   ├── units.py            # mm↔px, SVG 숫자 포맷
│   ├── pattern.py          # Pattern ABC
│   ├── tile.py             # <pattern> 조립
│   ├── repeat.py           # block / half_drop / brick 배치(2배 타일 트릭)
│   └── colorway.py
├── patterns/               # stripe / check / dot / herringbone
├── render/
│   ├── svg.py              # standalone SVG 문서 조립
│   └── raster.py           # resvg 서브프로세스 → Pillow 재인코딩(PNG/TIFF + DPI)
└── validate/seamless.py    # 경계 연속성 진단
tests/                      # pytest
```

## 테스트

```bash
.venv/bin/python -m pytest
```

래스터 테스트는 SVG 렌더러가 없으면 자동으로 건너뛴다(skip).

## 알려진 한계

- **저장소**: 패턴은 인메모리 저장이라 재시작 시 사라지고 멀티워커 간 공유되지 않는다.
