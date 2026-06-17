# 아키텍처 — AI Seamless SVG 생성기

텍스트 또는 참조 이미지로부터 **이음매 없는(seamless) 텍스타일 패턴 SVG**를 생성하는
서비스의 목표 아키텍처. LLM은 "의도"만 정하고, 좌표 배치와 seamless 보장 같은 정밀
작업은 결정론적 엔진이 책임진다.

## 목표 / 철학

- **LLM은 의도만**: LLM에게 raw SVG 좌표를 직접 그리게 하지 않는다. LLM은 짧은 "의도
  JSON"(무엇을, 몇 개, 어떤 색)만 출력하고, seamless·배치 같은 정밀 작업은 결정론적
  코드가 보장한다. 싼 모델로도 고품질이 나오고 출력이 짧아 비용이 낮다.
- **구조적 seamless(Seamless by construction)**: seamless 100%는 **엔진의 wrap/반복 격자
  로직이 책임진다(LLM 아님)**. 타일은 픽셀 보정이 아니라 반복 격자(repeat lattice)와
  경계 토러스 wrap으로 seamless가 된다.
- **벡터 우선(Vector-first)**: SVG가 단일 진실 공급원이며, PNG/TIFF는 SVG를 래스터화해
  파생한다. 인쇄 해상도 전 구간에서 무손실이다.
- **mm 기반 단위**: 기하는 내부적으로 모두 밀리미터로 다루고, `px = round(mm / 25.4 × dpi)`
  변환은 래스터 경계에서만 수행한다. DPI는 출력 파일에 기록한다.
- **최소 의존성**: SVG는 표준 라이브러리(f-string / `xml.etree`)로 조립한다. 래스터화는
  CLI 바이너리에 위임한다.
- **생성 ≠ 반복(Generation ≠ repeat)**: motif 생성과 반복 배치(block / half_drop / brick)는
  분리돼 있다.

## 제품 표면 (API)

**실제 제품 API는 단 하나다.**

```
POST /api/v1/generate
  body: {
    prompt: string,              # 텍스트 의도
    reference_image?: <image>,   # 선택. 있으면 image-to-image
    canvas?, palette?            # 선택 힌트
  }
  -> { id, svg, intent }         # seamless SVG + 사용된 의도 JSON
```

- `reference_image`가 **없으면 text 어댑터**, **있으면 text+image(멀티모달) 어댑터**로
  라우팅된다. 두 경로 모두 같은 **의도 JSON**을 생성하므로 엔진·검증 루프는 100% 공유된다.
- image-to-image는 참조 이미지로 스타일/모티프/색을 *해석*해 의도 JSON을 채울 뿐,
  좌표·seamless는 여전히 엔진이 보장한다. 그래서 이미지 입력이어도 seamless가 깨지지 않는다.

> **나머지 엔드포인트는 단계별 개발 확인용 스캐폴딩이다.** `patterns/stripe`, `dot`,
> `check`, `herringbone`, `export`, `colorway`, `palettes`는 엔진·래스터·seamless를
> 개별적으로 검증하기 위한 도구이며, 최종 제품 표면이 아니다. 이들은 의도 JSON을 거치지
> 않고 정밀 파라미터를 직접 받는다(엔진 단위 테스트 성격).

## 파이프라인 (의도 JSON → 엔진 → 검증 루프)

```
입력 ─┬─ text 어댑터:       prompt           ─┐
      └─ text+image 어댑터: prompt + ref_img ─┘
                                              │
                                              ▼
                                  LLM → 의도 JSON  (유일한 계약/seam)
                                              │
                                              ▼
                       [결정론적 엔진]  배치(count+seed→좌표) + wrap 복제 + viewBox/팔레트 고정
                                              │
                                              ▼
                       [검증 루프]  렌더 → 2x2 타일링 → seam 메트릭 + (멀티모달) LLM 피드백
                                              │ (문제 시 의도 JSON 수정 후 재생성)
                                              ▼
                                        seamless SVG
```

**① 입력 어댑터 → 의도 JSON.** 두 진입점은 LLM 입력만 다르고 출력 스키마는 동일하다.
의도 JSON은 좌표를 담지 않는다(엔진이 결정).

```jsonc
{
  "canvas": { "tile_mm": 48, "size": 1024 },
  "motifs": [
    { "id": "flower-coral", "type": "scatter", "count": 12, "rotation": "random", "scale": 0.9 }
  ],
  "palette": ["#10243a", "#ef8a7a", "#f5ca57"]
}
```

**② 결정론적 엔진.** `count`(+seed)로부터 배치 좌표를 결정론적으로 생성하고, 타일 경계를
넘는 오브젝트는 반대편에 복제(torus wrap)해 seamless를 수학적으로 보장한다. `viewBox`·
팔레트는 고정된다.

**③ 검증 루프.** 결과를 래스터화해 2x2로 타일링하고 경계를 확인한다. seam 메트릭
(`validate/seamless.py`)으로 정량 측정하고, 필요 시 렌더 이미지를 멀티모달로 LLM에 되먹여
의도 JSON을 수정한다.

## 레이아웃

모든 코드는 `app/` 패키지 아래에 있다(단일 import 루트, editable 설치 없음).
`[목표]`는 최종 제품 코어, `[확인용]`은 단계별 개발 검증 스캐폴딩이다.

```
app/
├── main.py                 # create_app(): 라우터를 /api/v1 아래에 연결
├── core/config.py          # pydantic-settings: renderer_bin, default_dpi, max_dpi, max_tile_mm
├── domain/                 # 프레임워크 비의존 코어  [목표]
│   ├── units.py            # mm<->px 변환, SVG 숫자 포맷
│   ├── colorway.py         # Colorway(hex, 순환) + 명명 팔레트(PALETTES)
│   ├── repeat.py           # RepeatMode + placements() (2배 타일 트릭)
│   ├── tile.py             # <pattern> 조립
│   └── pattern.py          # Pattern ABC: motif() + to_pattern_def()
├── patterns/               # Stripe / Check / Dot / Herringbone  [확인용: 엔진 단위 검증]
├── render/
│   ├── svg.py              # mm 단위 standalone SVG 문서
│   └── raster.py           # rsvg-convert|resvg 서브프로세스 -> Pillow (PNG/TIFF + DPI)
├── validate/seamless.py    # 이음매 메트릭 (seamless_diff, edge_seam)  [목표: 검증 루프의 측정부]
└── api/
    ├── routes/             # generate[목표] + health/patterns/export/palettes[확인용]
    ├── schemas/            # 의도 JSON 스키마[목표] + 패턴별 정밀 스키마[확인용]
    └── deps.py             # 인메모리 패턴 저장소 (id -> Pattern)
```

### 아직 만들어야 하는 부분 (얇은 신규 레이어)

- **의도 JSON 스키마** — LLM과 엔진 사이의 단일 계약.
- **scatter를 1급 엔진 타입으로** — 현재는 `scripts/generate_flower_scatter.py`에 하드코딩된
  좌표로만 존재. `count`(+seed) → 결정론적 배치 알고리즘으로 승격해야 한다.
- **motif 카탈로그/레지스트리** — LLM이 `id`로 참조할 motif 정의 집합.
- **LLM 어댑터** — text / text+image 입력 → 의도 JSON (function calling / tool use 또는 MCP).
- **검증 루프** — 렌더 이미지를 멀티모달로 LLM에 되먹이는 폐루프(현재는 측정 메트릭만 존재).

## Seamless 반복 (`domain/repeat.py`)

하나의 motif는 `W×H` 셀을 차지한다. `placements()`는 합성 타일(compound tile) 크기와,
그 합성 타일이 순수 평행이동 반복이 되도록 motif를 찍을 오프셋들을 반환한다.

- **block**: `(W, H)`, `(0,0)`에 찍음.
- **half_drop**: `(2W, H)`, `(0,0)`, `(W, H/2)`, `(W, -H/2)`에 찍음 — half-drop 격자의
  사각 주기 + 수직 wrap 복제본.
- **brick**: `(W, 2H)`, `(0,0)`, `(W/2, H)`, `(-W/2, H)`에 찍음.

산포(scatter)형은 셀 격자 대신 **경계 토러스 wrap**으로 처리한다: 오브젝트 bbox가 타일
경계를 넘으면 반대편에 복제본을 추가한다(`경계-r<0 → +period 복제`, `경계+r>period →
-period 복제`). 이 규칙이 도트마다의 모서리 계산 없이 seamless를 보장한다.

## 래스터화 (`render/raster.py`)

SVG → PNG는 CLI 서브프로세스로 변환하고(SVG를 stdin으로 전달), 이어서 Pillow가 물리
DPI를 박아 재인코딩한다(PNG `pHYs`; TIFF `XResolution`/`YResolution` + LZW). 픽셀 크기는
`width_mm × dpi`로 명시적으로 계산하므로 박힌 DPI가 일관된 메타데이터가 된다.

렌더러 우선순위: **rsvg-convert(librsvg) → resvg**. 가드: `dpi ≤ max_dpi`,
`width_mm ≤ max_tile_mm`, 그리고 픽셀 치수 하드 캡.

## 알려진 한계

- **저장소**: 패턴은 인메모리 저장이라 재시작 시 id가 사라지고 워커 프로세스 간 공유되지
  않는다.
- **미구현**: LLM 어댑터·의도 JSON·검증 루프·scatter 엔진 타입은 위 "신규 레이어"로 아직
  구축 전이며, 현재 코드는 결정론적 엔진과 측정 메트릭까지 완성된 상태다.
