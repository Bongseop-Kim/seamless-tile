# Spec — 결정론적 원단 텍스처 렌더 (deterministic fabric texturing)

승인된 seamless 타일에 **실제 원단 질감(올·짜임·엠보스)**을 입혀 "그래픽"이 아닌 "천" 느낌의 결과를
만드는 파생 출력 단계 설계 명세다. SVG는 벡터 룩이라 그대로는 실물 원단 느낌이 아니다.

**방향: 생성형 모델 없이 결정론·무료·로컬로 처리한다.** 입력이 이미 seamless인 이 서비스에서는 결정론
텍스처링이 정확히 맞는다 — 동일 입력=동일 출력, seamless 보장, 요청당 거의 공짜, 외부 egress 없음, 모델 없음.
(생성형 image repaint는 검토했으나 비결정·비용·egress·IP 사유로 **채택하지 않는다**.)

- 설계 기준: [ARCHITECTURE.md](../../ARCHITECTURE.md)(출력 경계·파생물·보안), 승인/세션 연계는
  [conversational-design-sessions.md](conversational-design-sessions.md)(finalize 단계).
- 관련 코드: `app/render/raster.py`(외부 렌더러로 SVG→PNG + **Pillow 재사용**),
  `app/render/sanitize.py`(allowlist — `<filter>` **설계상 거부**), `app/validate/seamless.py`(edge_seam 회귀 가드),
  `app/storage/`(Supabase 업로드), `app/api/routes/generate.py`·`export.py`(라우트 패턴), `app/core/config.py`.

---

## 0. 관통 원칙

- **결정론적 파생물.** preview PNG와 같은 범주다 — SVG가 canonical, 텍스처 렌더는 파생. 단 preview는
  민짜 rasterize, 이건 **텍스처를 입힌 rasterize**다. 렌더러·Pillow·텍스처 자산·파라미터를 고정하면 **재현 가능**하다.
- **seamless 유지.** 입력 타일이 seamless이고 텍스처를 **tileable + wrap 샘플링**으로 합성하므로 결과도 seamless.
- **엔진·SVG·보안 경계 무손상.** 깨끗한(필터 없는) SVG를 그대로 rasterize하고 텍스처는 raster 단계에서만
  입힌다 → `sanitize_svg` allowlist를 **건드리지 않는다**(§5.1).
- **거의 공짜.** 외부 API·모델 없음. 로컬 CPU 픽셀 연산.

---

## 1. 배경 & 문제

- 현재 출력은 결정론 SVG + 민짜 rsvg 프리뷰 PNG뿐(`app/render/`). 둘 다 그래픽 룩.
- 제품은 textile 서비스라, 확정 디자인을 **실물 원단 질감**으로 보고 싶다("느낌"이 목표).
- `/generate`는 stateless·slim response라 "사용자가 X 후보를 골랐다"는 결정 신호를 서버가 받는 경로가 없다.
  이 spec은 그 finalize 훅 + 텍스처 렌더 단계를 정의한다.

---

## 2. 접근 비교 & 선택

| | A. SVG 필터 (벡터) | **B. 래스터 합성 (선택)** |
|---|---|---|
| 방식 | `<filter>`(feTurbulence stitch + feDiffuse/SpecularLighting, feImage/feBlend, feDisplacement) | 민짜 rasterize 후 Pillow로 tileable 위브 multiply/overlay + normal-map relight |
| 결정론 | SVG bytes 결정론, 렌더 결과는 렌더러 의존 | **결정론(픽셀 연산 exact)** |
| seamless | filter stitch로 유지 | **wrap 샘플링으로 유지** |
| 비용 | 거의 공짜 | **거의 공짜** |
| 이 레포 적합성 | **낮음** — sanitizer가 `<filter>` 거부(§5.1), feImage는 no-embedded-raster 규칙과 충돌, 필터 지원이 librsvg에만(resvg 폴백은 부분) | **높음** — Pillow 이미 의존성, 기존 rasterize 재사용, 보안/렌더러 변경 0 |
| 결과 강도 | 가벼운 엠보스/캔버스 느낌 | **진한 올·실물 느낌** |

**선택: B (래스터 합성).** 이유는 §5.1(보안)·렌더러 폴백·Pillow 기보유. A는 벡터 배포가 꼭 필요하고
librsvg 고정 + sanitizer 확장을 감수할 때만(§8 대안). **생성형(Gemini 등)은 채택하지 않는다** — "느낌"에
과하고 비결정·비쌈·egress·IP 문제.

---

## 3. 결정 (Decisions)

`✅` 확정 · `❓` 착수 전 확인(§9).

| # | 결정 | 상태 | 비고 |
|---|---|---|---|
| R1 | 실사화 = **결정론 래스터 합성(B)**. **생성형 모델 미사용** | ✅ | §2, §0 |
| R2 | 입력 = 승인 SVG를 기존 `render/raster.py`로 rasterize한 PNG(민짜, 필터 없음) | ✅ | 양쪽 렌더러 OK |
| R3 | 텍스처는 **bundled tileable 위브 + (옵션) normal 맵**을 Pillow로 합성, **wrap 샘플링**으로 seamless | ✅ | Pillow 기보유 |
| R4 | 결과는 **재현 가능**(렌더러·Pillow·자산·파라미터 pin). preview와 동급 파생물 | ✅ | §0 |
| R5 | **sanitizer/보안 경계 무변경** — 필터를 SVG에 넣지 않으므로 allowlist 확장 불필요 | ✅ | §5.1 |
| R6 | finalize 트리거는 **UX 결정 단계**(이 후보로 확정 → 렌더). **비용 게이트 아님**(무료·로컬) | ✅ | 세션 spec §8.4 |
| R7 | 출력 PNG는 **Supabase Storage** 업로드 → URL(기존 프리뷰 패턴), 미설정 시 URL null + warning | ✅ | `app/storage/` |
| R8 | 원단 노브: `fabric`(cotton/linen/silk/knit/denim), `finish`(matte/sheen), `lighting`(방향/세기) → 자산/파라미터 선택 | ❓ | 노브별 자산 세트 §9 |
| R9 | 승인 후보의 **resolved intent**(label 렌더용) + SVG 조회: 세션 state(current_intent) / `(request_id, candidate_id)` 로그 / 클라이언트 전달 | ❓ | slim response가 숨김; §5.6이 intent 필요 |
| R10 | **영역별(per-region) 질감**: material map(region→fabric)을 label 렌더 + 마스크 합성으로 적용. 배경/스트라이프/모티프마다 다른 텍스처, 미지정은 기본 폴백 | ✅ | §5.6 |

---

## 4. 목표 아키텍처 (한 장)

```text
[사용자 결정] 후보 X 확정 (UX 버튼 — 무료라 비용 게이트 아님, R6)
        │
  승인 SVG 조회 (세션 state | 로그 (request_id,candidate_id) | 클라이언트 전달)      ← R9
        │
  render/raster.py 로 민짜 색상 PNG + compose(intent, label-colorway)로 region 세그맵  ← 기존 자산/compose (§5.6)
        │
  Pillow 합성 (결정론, region별):                                                    ← Pillow 기보유
    배경/스트라이프/모티프 region마다 material map이 지정한 tileable 텍스처를
    wrap 샘플링 multiply/overlay + normal-map relight, 마스크로 블렌드
    (미지정 region은 기본 질감 폴백; 균일 질감은 이 특수케이스)
        │
  (선택) validate/seamless edge_seam 가드로 연속성 확인                               ← §7
        │
  Supabase Storage 업로드 → 텍스처 렌더 URL 반환                                       ← app/storage
```

**불변식**: 깨끗한 SVG를 소비만 한다. SVG·엔진·sanitizer·결정론 출력 불변. 텍스처는 raster 단계에서만.

---

## 5. 컴포넌트 설계 (B)

### 5.1 왜 sanitizer를 안 건드리나 (A와의 결정적 차이)
`app/render/sanitize.py` allowlist에는 `<filter>`·fe* 가 **없고**, 주석이 명시한다: *"`<script>`/
`<foreignObject>`/`<image>`/`<filter>` are not listed, so injection and embedded raster are rejected by
construction."* 엔진 출력은 반드시 `sanitize_svg`를 통과하므로, A는 이 **의도적으로 좁은 allowlist를 filter
프리미티브로 확장**해야 하고 `feImage`(외부/임베드 래스터)는 보안 규칙과 정면 충돌한다. **B는 SVG에 필터를
넣지 않으므로 이 경계를 전혀 건드리지 않는다.**

### 5.2 입력 준비 (R2)
- 승인 SVG → `render/raster.py`로 목표 해상도 PNG(DPI cap·`MAX_DIMENSION_PX` 준수). 필터가 없으니
  librsvg·resvg 어느 쪽이든 동일하게 렌더.
- 기본 단일 타일. 필요 시 N×N 타일 패치 후 center-crop(패치도 seamless).

### 5.3 텍스처 합성 (R3)
- **bundled tileable 위브 텍스처**(fabric별) + (옵션) **tileable normal 맵**을 리포지토리에 포함(자산은
  결정론 입력의 일부 → 버전 pin).
- 합성: 위브를 `multiply`/`overlay`로 올리고(직조감), normal 맵으로 **고정 광원** relight(올이 도드라짐),
  (옵션) 미세 grain. 텍스처 샘플링은 **wrap(순환)** → 타일 경계에서 이어짐.
- 전 과정 Pillow(+선택 NumPy). 새 무거운 의존성 없음(Pillow는 이미 `raster.py`가 사용).

### 5.4 출력 / 저장 (R7)
- 텍스처 PNG → `app/storage/` Supabase 업로드 → URL. 미설정 시 URL null + warning(graceful).
- best-effort 로그: 어떤 후보를 어떤 fabric/finish/lighting으로 렌더했는지. **재현 가능**하므로 파라미터만
  기록하면 재생성 가능.

### 5.5 결정론 & 성능
- 픽셀 연산은 exact → **동일 (SVG, 자산, 파라미터, 렌더러/Pillow 버전) → 동일 PNG**. rasterize 자체는
  기존 preview와 동급의 렌더러-의존 파생(byte-identical은 pin된 렌더러 전제, ARCHITECTURE §검증 앵커 정신).
- 로컬 CPU, 요청당 거의 공짜. GPU·외부 호출 없음.

### 5.6 영역별(per-region) 텍스처링 — 배경/스트라이프/모티프마다 다른 질감 (R10)
목표: 한 장 균일이 아니라 **영역마다 다른 원단 질감**(예: 배경=cotton, 스트라이프=ribbed, 모티프=satin sheen).

- **material map**: `{ region → fabric/finish/lighting }`. region granularity = **레이어(layer_id) 또는 color
  slot**. 미지정 영역은 기본 질감으로 폴백 → §5.3 균일 동작은 이 특수케이스(material map 비었을 때).
- **label 렌더(세그멘테이션) — 기존 compose 재사용, 신규 엔진 코드 0**: colorway는 `slot→color` 매핑이므로,
  **각 region을 고유 플랫색으로 주는 "label colorway"**로 `compose(intent, palette, label_colorway)`를 한 번 더
  rasterize하면 곧 region별 세그멘테이션 맵이다. z-order 합성이라 겹치면 위 레이어가 픽셀을 소유 → 올바른
  영역 귀속. **label 렌더는 resolved intent가 필요** → finalize는 SVG만이 아니라 **intent**를 받는다(R9).
  - AA 경계: 렌더 경계 안티에일리어싱 중간색은 **nearest-label 귀속**(또는 소폭 erosion)으로 이산화.
- **per-region 합성**: 각 label 마스크에 배정된 텍스처를 **wrap 샘플링(타일 픽셀 공간)** + relight로 올리고
  마스크로 블렌드. **label 맵도 텍스처도 타일링되므로 결과도 seamless**(경계에서 같은 영역이 wrap).
- **결정론**: label 렌더 결정론 + 텍스처 tileable + 고정 파라미터 → 합성 결정론.
- (옵션·주의) region별 **텍스처 회전**(예: 실 방향을 stripe 각도에 맞춤)은 **tile-commensurate 각도일 때만**
  seamless 유지 — 기본은 축정렬. commensurability 정책은 엔진의 `snap_angle` 개념과 동일 선상.
- **granularity 한계**: slot 공유 시(예: 같은 색 스트라이프 밴드 여럿) 같은 label → 같은 질감. 진짜 "밴드마다/
  인스턴스마다" 다르게 하려면 slot이 아닌 **element-id 기반 label 렌더**가 필요(§9 열린 결정).
- **제어(세션)**: `set_material(target, fabric)` 편집 도구로 사용자가 배경/특정 스트라이프/특정 모티프 질감을
  지정(conversational-design-sessions §7). **material map은 엔진 intent가 아니라 세션/finalize 상태**에 둔다 —
  엔진은 material-agnostic이라 결정론 경계가 유지된다.

---

## 6. API 표면

- `POST /api/v1/finalize`(명칭 §9): 승인 후보 참조(R9) + 노브(R8) → 텍스처 렌더 URL.
  - **무료·로컬이라 비용 승인 아님**(R6). 세션 경로에선 "이 후보로 결정" UX 액션이 곧 이 호출.
  - 응답: `{ request_id, image_url, warnings[] }`. `X-Request-ID` 전파, 에러 바디 `detail`+`request_id`.
  - 에러: 참조 해석 실패 404/422, 렌더/스토리지 실패 502(기존 매핑).
- 세션 연계: conversational-design-sessions의 finalize 노드가 동일 로직 호출.

---

## 7. 수용 기준

- **텍스처 변환**: 승인 seamless SVG → 원단 질감 PNG URL, 육안상 그래픽이 아닌 천 느낌.
- **결정론(핵심)**: 동일 (SVG, 자산, 파라미터) → **동일 PNG**(pin된 렌더러/Pillow에서 byte 동일) — 테스트로 봉인.
- **seamless 유지**: 결과에 `validate/seamless.py` edge_seam 가드 적용. 결정론이라 임계값을 타이트하게 잡을 수 있음
  (텍스처 grain 감안한 소폭 tolerance). **영역별 질감을 적용해도** edge_seam 통과(label 맵·텍스처 모두 타일링).
- **영역별 질감(R10)**: material map으로 배경/스트라이프/모티프에 서로 다른 텍스처 지정 시 각 영역이 배정
  텍스처로 렌더되고 경계가 어긋나지 않음. material map 비었을 때 §5.3 균일 결과와 동일(폴백) — 테스트로 봉인.
- **보안 경계 무변경**: `sanitize.py` allowlist 미변경, 필터 없는 SVG만 rasterize — 회귀 테스트로 봉인.
- **무외부호출**: finalize가 외부 생성 API를 호출하지 않음(로컬) — 테스트로 확인.
- **결정 트리거**: finalize는 명시적 후보 결정으로 호출(자동 전부 렌더 금지) — 비용 게이트가 아니라 UX 단계.
- **degrade**: Storage 미설정 시 URL null + warning, 렌더러 미설치 시 명확한 502.

---

## 8. 대안: A (SVG 필터, 벡터 유지) — 조건부

텍스처를 **벡터 SVG 자체에 넣어 벡터로 배포**해야 할 때만. 조건: ① 렌더러를 **librsvg로 고정**(resvg 폴백은
필터 부분 지원), ② **procedural 서브셋만**(feTurbulence stitch + feDiffuse/SpecularLighting + feDisplacement;
`feImage` 외부 텍스처는 보안 규칙상 금지), ③ `sanitize.py` allowlist를 `<filter>`+fe* 프리미티브로 확장(보안
표면 확대 감수). 출력이 어차피 preview PNG이고 canonical SVG를 깨끗이 두는 설계라 이 레포에선 이점 약함 →
벡터 배포가 명시 요구일 때만.

---

## 9. 열린 결정 / 리스크

- **R8 자산 세트**: fabric/finish/lighting 노브별 tileable 위브·normal 맵 세트 범위(초기 1~2종으로 시작).
- **R10 granularity**: 영역 label을 **color slot 기준**(기본, 기존 colorway 재사용)으로 갈지 **element-id 기준**
  (밴드마다/인스턴스마다 구분, 엔진에 label 렌더 모드 추가 필요)으로 갈지. 초기엔 slot 기준으로 시작 권장.
- **region별 텍스처 회전**: 실 방향 맞춤을 지원할지(tile-commensurate 각도 제약) — 초기엔 축정렬만.
- **R9**: 승인 SVG 조회 경로 확정(세션 state / 로그 / 클라이언트 전달). slim response가 SVG를 숨김.
- **엔드포인트 명칭**: `/finalize` vs `/render-fabric`.
- **입력 형태**: 단일 타일 vs N×N 패치 center-crop — 질감 품질 실측.
- **리스크**: 텍스처 자산/파라미터가 결정론 입력의 일부이므로 버전 pin 필수. relight 광원·강도 과하면 seamless
  edge가 미세하게 흔들릴 수 있음 → §7 edge_seam 가드로 탐지, 임계값 실측 보정.
