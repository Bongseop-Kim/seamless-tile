# 아키텍처 — AI Seamless SVG 생성기

텍스트 또는 참조 이미지로부터 **이음매 없는(seamless) 텍스타일 패턴 SVG**를 생성하는
서비스를 목표로 한다. 현재 구현은 LLM 없이도 검증 가능한 결정론적 SVG 엔진과 확인용 API에
집중한다. LLM, 영속 저장소, 멀티모달 검증은 엔진 계약이 안정된 뒤 단계적으로 붙인다.

## 핵심 원칙

- **엔진은 LLM 비의존**: LLM은 최종적으로 `prompt -> intent JSON` 변환만 담당한다. raw SVG
  좌표, 배치, seamless 보장은 결정론적 엔진이 맡는다.
- **구조적 seamless**: 타일은 픽셀 보정이 아니라 반복 격자(repeat lattice), pattern
  transform, 경계 토러스 wrap으로 seamless가 된다.
- **벡터 우선**: SVG가 단일 진실 공급원이다. PNG/TIFF는 SVG를 래스터화한 파생 산출물이다.
- **mm 기반 단위**: 기하는 내부적으로 밀리미터로 다루고, `px = round(mm / 25.4 * dpi)` 변환은
  래스터 경계에서만 수행한다.
- **생성과 반복의 분리**: motif 정의, placement, repeat mode, raster export를 분리한다.
- **교체 가능한 외부 의존성**: 저장소와 LLM provider는 코어 엔진 바깥 어댑터로 둔다.

## 현재 구현

현재 API는 제품용 `generate`가 아니라 엔진 검증용 스캐폴딩이다.

```text
POST /api/v1/patterns/stripe
POST /api/v1/patterns/check
POST /api/v1/patterns/dot
POST /api/v1/patterns/herringbone
GET  /api/v1/patterns/{id}
GET  /api/v1/patterns/{id}/export?format=svg|png|tiff
POST /api/v1/patterns/{id}/colorway
GET  /api/v1/palettes
GET  /api/v1/health
```

이 API들은 정밀 파라미터를 직접 받아 SVG를 만들고, 엔진의 단위 기능을 확인한다. 최종 제품
표면은 후속 단계에서 `POST /api/v1/generate`로 별도 추가한다.

```text
app/
├── main.py                 # FastAPI app, /api/v1 라우터 연결
├── core/config.py          # renderer_bin, dpi, tile 크기 제한
├── domain/
│   ├── units.py            # mm <-> px 변환, SVG 숫자 포맷
│   ├── colorway.py         # Colorway + 명명 팔레트
│   ├── repeat.py           # block / half_drop / brick 배치
│   ├── tile.py             # <pattern> 조립
│   └── pattern.py          # 현재 패턴 ABC
├── patterns/               # stripe / check / dot / herringbone 구현
├── render/
│   ├── svg.py              # standalone SVG 문서 렌더링
│   └── raster.py           # rsvg-convert|resvg -> PNG/TIFF + DPI metadata
├── validate/seamless.py    # seam metric
└── api/
    ├── routes/             # 확인용 API
    ├── schemas/            # 확인용 request schema
    └── deps.py             # 개발용 인메모리 저장소
```

## 목표 엔진 계약

최종 `generate`는 직접 SVG를 요청하지 않고, LLM 또는 규칙 기반 어댑터가 만든 intent JSON을
엔진에 전달한다. intent JSON은 좌표를 담지 않는다.

```jsonc
{
  "canvas": { "tile_mm": 48 },
  "subject": "bee",
  "layout_id": "motif_scatter",
  "seed": 184231,
  "palette": ["#10243a", "#ef8a7a", "#f5ca57"],
  "motifs": [
    { "id": "bee", "count": 12, "scale": 0.9, "rotation": "random" }
  ]
}
```

엔진은 이 계약을 기준으로 다음 책임만 가진다.

- **Layout catalog**: 검증된 layout recipe 목록에서 compatible layout을 선택한다.
- **Layer primitives**: stripe, dot, check, herringbone 같은 기하 레이어를 재사용 가능한 조각으로
  제공한다.
- **Motif registry**: `bee`, `flower`, `pelican` 같은 motif SVG defs를 id로 제공한다.
- **Placement engine**: grid, periodic, scatter, diagonal lane 배치를 seed 기반으로 결정한다.
- **Composition engine**: background layer, helper layer, motif layer를 하나의 SVG로 합성한다.
- **Seamless guarantee**: repeat mode와 torus wrap으로 경계 연속성을 보장한다.

## 제품 API 목표

최종 제품 표면은 하나로 좁힌다.

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
- `reference_image`가 있으면 image-to-image 경로로 처리하되, 참조 이미지는 스타일/모티프/색을
  intent JSON으로 해석하는 데만 사용한다.
- 포괄 요청은 여러 compatible layout 후보를 반환한다.
- 구체 요청은 `candidate_count=1`로 줄일 수 있다.
- 같은 `seed`와 같은 intent는 같은 SVG를 재생성해야 한다.

## 개발 순서

1. **Intent schema**: LLM 없이도 손으로 호출 가능한 최소 intent JSON 스키마를 만든다.
2. **Placement engine**: scatter와 periodic motif 배치를 1급 엔진 기능으로 올린다.
3. **Motif registry**: MVP motif로 `bee`를 추가하고, `flower`/`pelican`은 fixture로 유지한다.
4. **Layout recipe**: `stripe_dot_motif`, `stripe_motif`, `motif_periodic`, `motif_scatter`를
   얇은 recipe로 구현한다.
5. **Generate API**: `POST /api/v1/generate`를 추가하되, 초기에는 LLM 없이 규칙 기반 intent
   builder로 연결한다.
6. **Storage adapter**: 개발용 인메모리 저장소를 저장소 인터페이스 뒤로 숨기고, 산출물 저장은
   Supabase Storage로 교체한다.
7. **LLM adapter**: `prompt -> intent JSON` 어댑터를 붙인다. provider API는 이 단계 이후에
   선택한다.
8. **Validation loop**: seam metric 기반 검증을 먼저 자동화하고, 필요하면 멀티모달 LLM 피드백을
   후속으로 붙인다.

## Seamless 반복

`domain/repeat.py`의 `placements()`는 motif base cell을 순수 평행이동 반복 가능한 compound tile로
배치한다.

- **block**: `(W, H)`, `(0, 0)`.
- **half_drop**: `(2W, H)`, `(0, 0)`, `(W, H/2)`, `(W, -H/2)`.
- **brick**: `(W, 2H)`, `(0, 0)`, `(W/2, H)`, `(-W/2, H)`.

scatter 계열은 셀 격자 대신 경계 토러스 wrap을 사용한다. 오브젝트 bbox가 타일 경계를 넘으면
반대편에 복제본을 추가한다.

## 래스터화

`render/raster.py`는 SVG를 CLI 렌더러에 넘겨 PNG/TIFF로 변환한다.

- 렌더러 우선순위: `rsvg-convert` -> `resvg`
- PNG: `pHYs` DPI metadata 기록
- TIFF: `XResolution`/`YResolution` + LZW
- 가드: `dpi <= max_dpi`, `width_mm <= max_tile_mm`, 픽셀 치수 하드 캡

## 현재 범위와 후속 단계

- **저장소**: 현재는 개발 확인용 인메모리 저장소를 사용한다. 재시작 시 id가 사라지고 워커
  프로세스 간 공유되지 않는다. 후속 단계에서 저장소 인터페이스를 만들고, SVG/PNG/TIFF 산출물은
  Supabase Storage로 저장한다. 요청 이력, 후보 메타데이터, 사용자 소유권 같은 조회 데이터가
  필요해지면 Supabase Postgres를 별도로 둔다.
- **LLM 연결**: 현재 엔진은 LLM 없이 동작해야 한다. LLM은 후속 단계에서 intent JSON 생성
  어댑터로 붙인다. 엔진은 provider 종류를 알지 않는다.
- **image-to-image**: 참조 이미지는 후속 범위다. 추가되더라도 좌표와 seamless 보장은 엔진이
  담당하고, 이미지 입력은 intent JSON 해석에만 사용한다.
- **검증 루프**: 현재는 seam metric과 수동 확인을 기준으로 한다. 멀티모달 LLM 피드백 루프는
  LLM 어댑터 이후 단계로 미룬다.
