# `/api/v1/generate` — Prompt 시나리오 검증 가이드

prompt 입력 경로만 검증한다 (`intent`·`reference_image` 미사용). 로컬 서버를 띄우고 요청은 직접 보내며 결과를 하나씩 확인한다.

> **응답 슬림화(v2)**: generate 응답은 `request_id`, `candidates[].{id, png_url}`, `warnings[]`만 포함한다. `svg`·`intent`·`layout_id`·`source_fidelity`·`repro`는 응답에서 제거되어 `seamless_generation_logs` 테이블(admin "Seamless 생성 로그")에 기록된다. 아래 시나리오에서 이들 필드 확인은 **admin 로그 상세** 기준으로 검증한다. `png_url`은 Supabase Storage에 렌더된 미리보기 PNG의 public URL(storage 미설정 시 `null` + warning).
> **비용 주의**: prompt 요청은 매번 Gemini를 호출하고(저렴하지만 무료 아님), detailed/멀티컬러 모티프는 **Recraft 생성(고비용 💰)** 을 유발한다. 동일 입력 재호출은 freeze 캐시로 외부 호출 0(무료). 아래 등급·실행 순서를 따라 비용을 최소화하라.

---

## 동작 모델 (prompt-only 경로)

```text
prompt(str) → Gemini gemini-2.5-flash-lite → designs[] = [{intent, motif_specs}, ...]
            → design별 motif_specs resolve: 캐시/store 매치 or 생성
                 · complexity="simple"   → LLM(Gemini)로 모티프 SVG 생성
                 · complexity="detailed" → Recraft로 모티프 생성 💰
            → 결정론 엔진: design별 candidate 생성 → SVG dedup/round-robin merge
            → merged candidates[] SVG
```

알아둘 점:

- 같은 `(prompt, canvas, palette)`는 freeze 캐시로 Gemini 0회 재호출. 모티프도 spec 단위 freeze → 같은 spec 재요청 시 생성 0회.
- 결정론: 같은 `prompt + seed + colorway` → **바이트 동일 SVG**. 재현성 테스트의 2번째 호출은 캐시로 무료.
- 내장 모티프 `circle`, `bee`는 생성 없이 직접 참조 가능.
- `source_fidelity`는 prompt 경로에서 **항상 `"vector"`** (이미지 경로 전용이 `raster_hybrid`). → **Recraft 사용 여부 판별 불가**. 판별은 intent의 motif `colors` dict(멀티컬러) + **서버 로그의 외부 호출**로 한다.
- HTTP 코드: **422**(스키마/시맨틱 실패), **502**(LLM·Recraft 미구성/외부 실패), **500**(엔진 합성 실패), **200 + warnings**(부분 성공).

---

## 비용 등급

| 등급     | 의미                                              | 해당 그룹       |
| -------- | ------------------------------------------------- | --------------- |
| 🟢 저    | Gemini intent 1회만 (모티프 생성 없음)            | A, 대부분의 D/E |
| 🟡 중    | Gemini intent + Gemini 모티프 생성 (Recraft 없음) | B               |
| 🔴 고 💰 | Recraft 모티프 생성 (spec당 1회)                  | C               |
| ♻️ 무료  | 동일 입력 재호출 = 캐시 히트, 외부 호출 0         | G 2번째 호출, J |

---

## 공통 확인 체크리스트 (모든 200 응답)

- [ ] HTTP 200, body에 `request_id`, `candidates[]`, `warnings[]`
- [ ] `len(candidates)` == 요청 `candidate_count` (기본 4). 부족 시 `warnings`에 다양성 부족 메시지
- [ ] 각 `candidates[]`는 `id`, `png_url`만 포함(슬림화). `id` 중복 없음. `png_url`은 Storage 미리보기 URL(미설정 시 `null` + warning)
- [ ] **아래는 응답이 아닌 `seamless_generation_logs`(admin) 상세에서 확인** (svg·intent·layout_id·source_fidelity·repro는 응답에서 제거됨):
  - [ ] `candidates[].svg`: `<svg`로 시작, `<pattern ... patternUnits="userSpaceOnUse">` 포함, `<script>`·`<image>` 등 미허용 태그 없음
  - [ ] `candidates[].layout_id` 서로 다름(다양성)
  - [ ] `candidates[].repro`: `seed`, `colorway_id`, `layout_id`, `engine_version`, `registry_version`, `intent_version == 1`
  - [ ] `intent` 구조가 시나리오 의도와 일치 (시나리오별 확인)
- [ ] 터미널/서버 로그의 외부 호출 횟수 == 예상 비용 등급

기본 요청:

```bash
curl -s http://localhost:8000/api/v1/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"<프롬프트>"}' | jq .
```

> prompt는 영어 평문으로 작성한다(LLM 기대 입력). 보조 파라미터(`seed`/`colorway`/`candidate_count`/`canvas`/`palette`)는 표시된 시나리오에서만 사용한다.

---

## 그룹 A — 내장 모티프 / 무생성 (🟢 우선 실행)

### A1. 대각 스트라이프 (넥타이 기본)

- **목적**: stripe 레이어 + 대각 슬로프 스냅(넥타이 도메인 기본), 모티프 미생성 happy path
- **prompt**: `Classic diagonal repp stripe necktie pattern, navy ground with silver and gold stripes, evenly spaced.`
- **확인**: `intent.layers`에 `type:"stripe"` 존재 · `params.angle` 음수(화면상 우상향 대각) · `period_mm`가 `tile_mm`의 약수 · 모티프 레이어/`motif_specs` 없음 · **모티프 생성 로그 0** · candidate 4개 layout 다양

### A2. 폴카도트 (내장 `circle` 기대)

- **목적**: 내장 `circle` 직접 참조(생성 0), 격자/스캐터 배치
- **prompt**: `Evenly spaced simple circular polka dots on a solid background, two colors.`
- **확인**: motif 레이어 `params.motif_id == "circle"`(내장 재사용이면 이상적) · 생성 호출 0 · 신규 모티프를 생성했다면 "내장 미사용" 발견사항으로 기록

### A3. bee on stripe (내장 `bee`)

- **목적**: 내장 `bee` + path_following(스트라이프 레인 배치)
- **prompt**: `Small bees arranged along diagonal stripes, navy and gold necktie style.`
- **확인**: stripe 레이어 + `type:"motif"` `motif_id=="bee"` · placement `type:"path_following"`, `host_layer`가 stripe id 참조, `lane` 지정 · 생성 0

---

## 그룹 B — 단색 신규 모티프: LLM 생성 (🟡 Gemini만)

### B1. 단색 기하 모티프 (다이아몬드)

- **목적**: 내장에 없는 단색 모티프 → `complexity:"simple"` → **LLM(Gemini) 생성** 경로
- **prompt**: `Repeating pattern of small single-color diamonds in a regular grid, one ink color on a light ground.`
- **확인**: `motif_specs` 1개 항목, `scope` ∈ {whole, partial}, complexity simple(또는 미지정) · motif 레이어가 단색 `params.color`(슬롯 id) 사용 · **Recraft 로그 0**, LLM 생성 호출 발생 · svg 유효

### B2. 단색 라인아트 잎

- **prompt**: `Minimal single-color line-art leaf motif scattered evenly, monochrome.`
- **확인**: B1과 동일 패턴 · 단색(`color`) · Recraft 미호출 · placement는 scatter/lattice 중 하나

---

## 그룹 C — 디테일/멀티컬러 모티프: Recraft 생성 (🔴 고비용 💰, 최소 실행)

### C1. 회화풍 멀티컬러 플로럴

- **목적**: `complexity:"detailed"` + 멀티컬러 → **Recraft 생성** 경로, 멀티컬러 슬롯 바인딩
- **prompt**: `Lush painterly floral with shaded multi-color petals and leaves, rich detailed botanical, half-drop repeat.`
- **확인**: `motif_specs` complexity `"detailed"` · motif 레이어 `params.colors`(dict, 슬롯 다수) — 멀티컬러 경로 신호 · **서버 로그에 Recraft 호출 발생** · 생성 SVG가 gate/sanitize 통과(그라디언트 평탄화·배경 제거 후) · 200, 정상 합성
- **비용 주의**: motif_spec 개수만큼 Recraft 호출. 프롬프트에서 모티프 종류를 1개로 한정해 비용 억제.

### C2. 디테일 페이즐리 (선택, 💰)

- **prompt**: `One ornate detailed paisley motif with fine internal shading, arranged on diagonal lanes.`
- **확인**: C1과 동일 · Recraft 1회 부근 · 생성 실패(비-SVG/래스터) 시 → 1회 재시도 후 모티프 드롭(부분성공 200+warning) **또는** 502. 어느 쪽인지 기록

---

## 그룹 D — placement(배치) 모드 (🟢/🟡)

### D1. 하프드롭 리핏

- **prompt**: `A single flower motif in a half-drop repeat across the tile, two colors.`
- **확인**: placement `type:"lattice"`, `lattice.drop_fraction ≈ 0.5`, `drop_axis` 지정 · cell 치수가 tile 정수 분할

### D2. 브릭(오프셋) 리핏

- **prompt**: `Brick-offset repeat of a small motif, rows shifted by half, two colors.`
- **확인**: lattice + 행 방향 드롭/오프셋 · 정수 분할

### D3. 토스/스캐터 (랜덤)

- **prompt**: `Randomly tossed small leaves scattered evenly, non-directional, two colors.`
- **확인**: placement `type:"scatter"`, `scatter.mode:"poisson"`, `min_dist_mm <= tile_mm/2` · seed 변경 시 배치 달라짐(G2)

### D4. 정규 격자

- **prompt**: `Regular evenly spaced grid of identical small motifs, two colors.`
- **확인**: lattice block(드롭 없음) · cell 분할 정합

### D5. 사틴(sateen) 분산 (관찰용)

- **prompt**: `Dots arranged in a satin/sateen scatter so no two are aligned, single color.`
- **확인**: scatter `mode:"sateen"`이면 `gcd(sateen_step, sateen_n) == 1` 검증 통과 · LLM이 poisson/lattice로 해석할 수 있음 → 실제 생성된 placement 기록(프롬프트 강제 어려움)

---

## 그룹 F — colorway / palette (🟢, 보조 파라미터)

### F1. 다중 colorway 생성 + 전환

- **목적**: 프롬프트가 여러 colorway를 만들고, `colorway` 파라미터로 출력 색을 전환
- **prompt**: `Diagonal stripe necktie pattern with a default navy colorway and an alternate crimson colorway.`
- **요청 2회**:
  1. `{"prompt":"...", "seed":42}`
  2. `{"prompt":"...", "seed":42, "colorway":"<1번 응답의 대체 colorway id>"}`
- **확인**: `intent.colorways`에 `id:"default"` + 대체 1개(모든 slot 매핑) · 두 응답의 **`layout_id` 동일**(지오메트리 불변), 색만 다름 · `repro.colorway_id`가 요청값 반영 · 2번째 호출 LLM 0회(♻️)

### F2. 모노크롬

- **prompt**: `Monochrome black and white geometric pattern, high contrast.`
- **확인**: palette slots 2개 내외 · 단색 모티프 · 정상 합성

### F3. 스크린 프린트 색 제한

- **prompt**: `For screen printing, limit to 6 spot colors, bold geometric repeat.`
- **확인**: `intent.production.method == "screen"` 추정 · distinct color ≤ `max_colors` 검증 통과(초과면 422) · 색 수 확인

---

## 그룹 G — 결정론 / 재현성 (♻️ 2번째 호출 무료)

### G1. 동일 입력 → 바이트 동일

- **요청 2회 동일**: `{"prompt":"Diagonal stripe necktie, navy and gold.", "seed":7}`
- **확인**: 두 응답 candidates의 svg가 **완전 동일**(diff 0) · 2번째 호출 생성/LLM 로그 0(캐시) · repro 동일

### G2. seed만 변경 → 같은 레이아웃군, 다른 변형

- **요청**: 동일 prompt(스캐터 계열, 예 D3), `seed:1` vs `seed:2`
- **확인**: 배치/변형 픽이 달라짐 · 구조적 `layout_id` 계열은 동일(seed는 layout_id 비포함) · 둘 다 seamless

### G3. colorway만 변경 → 지오메트리 불변

- F1로 커버 (`layout_id` 동일, 색만 변경)

---

## 그룹 H — candidate_count / canvas / production (🟢, 보조 파라미터)

### H1. candidate_count 경계

- **요청**: 동일 prompt로 `candidate_count:1`, 그리고 `candidate_count:8`
- **확인**: 1 → candidate 1개 · 8 → 최대 8개, 전략 부족 시 `warnings`에 다양성 부족 + 실제 개수 < 8 가능 · 범위 밖(`0` 또는 `9`) → **422**(Pydantic 바디 검증)

### H2. canvas 오버라이드

- **요청**: `{"prompt":"...", "canvas":{"tile_mm":96,"dpi":300}}`
- **확인**: `intent.canvas.tile_mm` 반영 · period/spacing 약수 정합 유지 · 큰 타일에서도 seamless

### H3. dpi 클램프 (엣지)

- **요청**: `{"prompt":"...", "canvas":{"tile_mm":48,"dpi":240}}`
- **확인**: 200 + `warnings`에 정확히 `canvas.dpi 240 not in (150, 300, 600); clamped to 300` · `repro`/`intent` dpi == 300

---

## 그룹 I — 엣지 / 실패 처리 (🟢/🟡)

### I1. 과소 명세

- **prompt**: `flowers`
- **확인**: LLM이 기본값으로 유효 intent 생성 → 200 (canvas 기본 48mm/300dpi, default colorway 등) · 실패 시 422 `detail` 확인

### I2. 과다/장문 명세 (리소스 캡)

- **prompt**: `An extremely complex pattern with dozens of different flowers, twenty colors, many overlapping layers, intricate borders, and detailed scrollwork everywhere.`
- **확인**: 캡 내로 수렴(layers ≤ 64, palette slots ≤ 64, colorways ≤ 32) · 초과 요구는 422 또는 LLM이 캡 내 생성 · 합성 svg ≤ `max_svg_bytes`(2MB), 초과면 422

### I3. 비-텍스타일 / 무의미 입력

- **prompt**: `Quarterly revenue forecast for the marketing department.`
- **확인**: LLM이 임의 패턴 생성(200) 또는 검증 실패(422 detail 리스트) — **둘 중 무엇이든 500 없이 graceful**한지. 결과 기록

### I4. 빈 문자열 prompt

- **요청**: `{"prompt":""}`
- **확인**: `""`는 None이 아니므로 LLM 경로 진입 → 기본 패턴 생성(200) 또는 422. 비정상(500 등) 없음 확인
- 참고: prompt·intent·reference_image **전부 없으면** 422 `one of \`intent\`, \`reference_image\`, or \`prompt\` is required` — 빈 문자열은 이 분기 아님

---

## 그룹 J — 캐시 / store 재사용 (♻️ cross-request, C 실행 후)

### J1. 동일 detailed 프롬프트 재호출 → Recraft 0

- **전제**: C1을 1회 실행한 뒤
- **요청**: C1과 **완전히 동일한** prompt 재호출
- **확인**: 2번째는 Recraft·LLM 생성 로그 0(freeze 캐시 히트) · 동일 motif_id · svg 바이트 동일

### J2. 유사하지만 다른 프롬프트 → store soft-match (선택)

- **요청**: C1과 의미상 유사하나 문구가 다른 프롬프트, 예: `A detailed painterly floral with shaded petals, botanical, half-drop.`
- **확인**: 임베딩 soft-match(τ 기본 0.6 이상)면 store 모티프 **재사용**(생성 0) · 미달이면 신규 생성(🔴) · 어느 경로인지 로그로 기록 · (Supabase 미구성이면 항상 생성)

---

## 부록: 응답 필드 의미

| 필드                                                         | 위치 | 의미 / 확인 포인트                                                             |
| ------------------------------------------------------------ | ---- | ------------------------------------------------------------------------------ |
| `request_id`                                                 | 응답 | 요청 추적 id (`X-Request-ID` 헤더와 동일)                                      |
| `candidates[].id`                                            | 응답 | 결정론적 candidate id (intent+seed+colorway 파생)                              |
| `candidates[].png_url`                                       | 응답 | Supabase Storage 미리보기 PNG public URL (미설정 시 `null`)                    |
| `warnings[]`                                                 | 응답 | dpi 클램프, 다양성 부족, 모티프 드롭, preview 미설정(부분성공) 등              |
| `candidates[].svg`                                           | 로그 | 최종 seamless SVG. `<pattern ... userSpaceOnUse>`, 미허용 태그 없음            |
| `intent`                                                     | 로그 | LLM이 만든 + 해석된 `designs[]` 묶음. design별 레이어/배치/대칭/색 검증의 핵심 |
| `candidates[].layout_id`                                     | 로그 | 구조 지문(seed·colorway 제외). 다양성/결정론 비교 키                           |
| `candidates[].source_fidelity`                               | 로그 | prompt 경로는 항상 `vector` (Recraft 판별용 아님)                              |
| `candidates[].colorway_id`·`seed`, `engine/registry_version` | 로그 | 재현성 증빙(repro). 로그 row 컬럼 + candidates jsonb                           |
| HTTP 422                                                     | —    | 스키마/시맨틱 실패. `detail`은 문자열 리스트                                   |
| HTTP 502                                                     | —    | LLM/Recraft 미구성 또는 외부 실패                                              |
| HTTP 500                                                     | —    | 엔진 합성 실패(어떤 candidate도 못 만듦)                                       |

---

## 실행 순서 권장 (비용 최소화)

1. 🟢 A → D → F2/F3 → H → I (Recraft 무관, 저비용)
2. ♻️ G(결정론), F1(colorway 전환) — 2번째 호출 무료
3. 🟡 B (LLM 모티프 생성)
4. 🔴 C1 (Recraft 1회) → 직후 J1(캐시 히트 무료 확인) → 필요 시 C2/J2
5. 매 실행마다: 공통 체크리스트 + 시나리오별 확인 + 로그의 외부 호출 횟수 대조
