# Spec — 멀티컬러 모티프 라이브러리 & 프롬프트 기반 생성

프롬프트 기반 디자인 생성 서비스를 위해, **모티프를 다채롭게 생성·재사용·영속화**하고
**멀티컬러**로 합성하기 위한 설계 명세다. 기존 `docs/plan/*`(세션 1–8)이 만든 결정론 엔진
위에 얹는 상위 기능층을 정의한다. 본 문서는 *설계 기준점*이며, 구현은 이후 세션 단위로 분해한다.

관련 기존 문서: `docs/plan/06-product-api-and-ops.md`(제품 API),
`07-llm-and-reference-image-adapters.md`(prompt→intent), `08-security-hardening-and-recraft.md`(Recraft intake).

---

## 1. 배경 & 문제

현재 상태(코드 기준):

- 입력 경로는 3가지: `intent`(직접·내부용) > `reference_image` > `prompt`. 셋 중 하나 필수
  (`app/api/routes/generate.py`). 우선순위가 있고, `intent`를 주면 나머지는 무시(+warning).
- `prompt` 경로는 `llm.build_intent()`가 **intent JSON만** 생성. LLM은 SVG를 그리지 않으며
  (`_build_prompt`가 명시적으로 금지), `motif_id`는 **이미 등록된 것 중에서만** 고른다
  (`app/adapters/llm.py`).
- 내장 모티프는 `circle`, `bee` 둘뿐. 커스텀은 `normalize_motif_svg()` + `register_motif()`로
  등록해야 쓸 수 있다(`app/motifs/registry.py`).
- 모티프 레지스트리는 **인메모리 전역 dict**(`MOTIFS`) — 재시작 시 소실, 인스턴스 간 비공유.
- 모티프는 **단색 전용**. `normalize_motif_svg`의 `_recolor_to_slot`이 모든 색을 `currentColor`로
  강제하고, 멀티컬러(`MotifParams.colors`)를 주면 `compose()`가 에러를 던진다
  (`app/engine/composition.py:111` "multi-color `colors` binding is out of scope").

해결할 문제:

1. **멀티컬러 모티프 합성** (서비스 필수).
2. **프롬프트 → 모티프 생성** 플로우 부재(현재 LLM은 등록된 모티프만 참조).
3. **모티프 영속화 부재**(인메모리).
4. **다양성**: "돼지→SVG 하나" 같은 거친 매핑이 아니라, 명세 단위 카탈로그 + 변형 샘플링.

---

## 2. 확정된 결정 (Locked Decisions)

| # | 결정 | 비고 |
|---|---|---|
| D1 | 멀티컬러 모티프 지원은 **필수** | Recraft 산출물을 살리려면 선결 |
| D2 | 서비스 입구는 **prompt(+reference svg)**, `intent` 직접은 내부용 | 기존 설계와 일치 |
| D3 | 매핑 키는 동물이 아니라 **구조화된 모티프 명세** | subject/view/part/expression/style |
| D4 | 모티프는 **Supabase(Postgres + pgvector)에 영속화**(작은 텍스트 SVG → TEXT/JSONB), facet+임베딩 메타 포함 | 원본 대용량 에셋이 생기면 Supabase Storage |
| D5 | **조회 먼저 → 없으면 생성**(generate-on-miss) + **검수 후 승격** 루프 | 품질 바닥 + 비용/일관성 |
| D6 | **변형 샘플링** 사용: 명세당 변형 풀에서 선택 | 창작 서비스 톤 |
| D7 | 변형 선택은 **시드 기반(결정론)**, 랜덤 금지 | 결정성 계약 보존 |
| D8 | 모티프 생성 소스: **단순=LLM / 정교=Recraft** 로 라우팅, 공존 | |
| D9 | LLM 역할 = **intent + 모티프 명세(들)** 산출. SVG는 별도 단계 | 한 응답에 JSON+SVG 혼합 금지 |
| D10 | facet 어휘는 **주관식 위주 하이브리드**: `subject`·`part`만 통제 어휘(가드레일), 나머지(`expression`·`style`·설명문·tags)는 자유 텍스트+임베딩 | 객관식/주관식은 *검색* 축일 뿐 창의성과 무관 |
| D11 | 생성 소스 라우팅: LLM이 명세에 `complexity(simple\|detailed)` 힌트 산출 + 규칙(`simple→LLM`, `detailed 또는 멀티컬러 요구→Recraft`). 기본 LLM, 오버라이드 가능 | 비용↔품질 균형 |
| D12 | 임베딩=채팅 LLM과 **별개 모델**. **임베딩 = OpenAI `text-embedding-3-small`, 채팅 LLM = Gemini 2.5 Flash-Lite/Flash**(최신 모델 나오면 교체 가능). 임베딩 대상은 **LLM이 정규화한 영문 descriptor** | Claude엔 native 임베딩 없음. 모델 확정 → τ 실측 가능 |
| D13 | 조회는 **2단계 매칭**: 하드 필터(`subject`·`part`) → 소프트 유사도(τ, 뉘앙스만). 단, **정확매칭 우선**(아래 D18) 이후 임베딩. τ 시작값 재사용 우선 + 실측 보정 | 단일 임계 한계 해소 |
| D14 | 미스 생성 모티프는 **즉시 제공**(Tier1 자동: `sanitize`+구조검사 통과 시 요청자에게 바로, `status='auto'`). 공유 샘플링 풀 편입(`curated`)은 **사람 수동 검수**. **비전 LLM 의미검사는 비용상 보류** | UX(즉시) + 품질 바닥(풀은 검수본만) |
| D15 | 멀티컬러는 **"색 굽기(bake)" 폐기** → 엔진의 `<use color>` 단색 교체를 **슬롯 다개로 확장**. `<symbol>`은 colorway-무관(슬롯 참조만), 색은 인스턴스(`<use>`)에서 바인딩 | (리뷰 C1) 굽기는 content-hash id·symbol dedup·결정성 파손 |
| D16 | `variant_group` 키 = **결정론적**: `hash(subject + part + 정규화 핵심 facet)`. miss 모티프 합류 = 하드필터 동일 + τ 이상이면 기존 group, 아니면 신규 | (리뷰 M4) 풀·재현 토대 |
| D17 | 재현은 **resolved-intent 스냅샷**으로 닫는다(엔진은 concrete-motif intent만 받음). 풀은 가변 전역이므로 풀 변경 시 `registry_version` bump | (리뷰 C2·M5) `repro`엔 motif_id 필드 없음 |
| D18 | 콜드스타트/저비용 위해 **정확매칭(exact descriptor + 하드필터) 우선** → 미스일 때만 임베딩 유사도. ivfflat 인덱스는 행 수가 충분해진 뒤 도입(소량은 seq scan) | (리뷰 M3) 콜드스타트 dead-code 완화 |

비범위(이번 spec 밖): 참조 이미지(`reference_image`) 경로 고도화, 모티프 업로드 공개 API
(외부 통제 비계획), 오브젝트 스토리지(원본 대용량 보관).

---

## 3. 목표 아키텍처 (한 장)

```text
[서비스 입구] prompt (+reference svg)            [내부] intent 직접
                    │
        LLM: intent + 모티프 명세(들)
                    │
   ┌──────── 모티프 해석/획득 (오케스트레이션 글루) ─────────┐
   │  명세 → 의미검색(pgvector)                              │
   │   ├ hit  → 변형 풀에서 seed로 1개 선택                  │
   │   └ miss → 생성(단순=LLM / 정교=Recraft)               │
   │            → normalize_motif_svg → register            │
   │            → DB 영속화 → (검수 후 풀 편입)              │
   └─────────────────────────────────────────────────────────┘
                    │  concrete motif_id 확정 (intent에 박음)
        엔진 compose (멀티컬러 + colorway) → seamless SVG 후보 N개
                    │
        각 후보는 resolved-intent 스냅샷(concrete motif_id 포함)을 보존 → 재현 단위
```

**불변식**: 엔진은 항상 *구체적 `motif_id`가 확정된* intent만 받는다. 비결정 단계(LLM·Recraft·
샘플링)는 엔진 경계 밖에서 끝나므로, `(intent_version + intent + seed + colorway) → 바이트 동일
SVG` 계약은 그대로 유지된다(`docs/plan/00-overview.md` 공통 규약).

---

## 4. 멀티컬러 모티프 (엔진 변경 — D1)

### 4.1 현재 한계
- `_recolor_to_slot`: 모든 `fill`/`stroke`를 `currentColor`로 치환 → 단색.
- `<use href=...>` + `currentColor`는 인스턴스당 **한 색**만 주입 가능.
- `compose()`는 `colors` 입력을 거부.

### 4.2 접근 (`<use color>` 메커니즘의 슬롯 다개 확장 — D15)

엔진은 이미 단색 colorway 교체를 `<use color="#hex">` + symbol 내 `currentColor`로 깔끔히 한다
(`composition.py:119,127`). 멀티컬러는 이걸 **여러 슬롯으로 확장**한다 — `<symbol>`은
**colorway-무관**하게 유지(색을 굽지 않음), 색은 **인스턴스(`<use>`) 단위로 바인딩**.

> **왜 "굽기(bake)"를 폐기했나 (리뷰 C1)**: motif id는 정규화 기하의 sha256이고 composition은
> `setdefault(motif.id, symbol)`로 **motif_id 단위 dedup**한다(`composition.py:36,117`). 색을 symbol에
> 구우면 colorway마다 본문이 달라지는데 id는 기하 기준이라 → **첫 colorway 색이 모든 colorway에
> 재사용되는 색 오염** 버그. id에 colorway를 넣으면 "같은 그림=같은 id" 멱등성(§5.2)이 깨진다.
> 게다가 후보 팬아웃은 **후보당 colorway 1개**(`candidates.py`)라 굽기의 동기(런타임 색교체 회피)도 약하다.

1. **`normalize_motif_svg`**: 색을 단일 `currentColor`로 뭉개지 말고 **모티프-로컬 슬롯**(`s0,s1,…`)으로
   보존. `MotifDef`에 `color_slots: list[str]` 추가.
   - 색→슬롯 할당은 **문서 DFS 첫 등장 순**으로 고정(결정론). id 해시는 **슬롯화된 기하**(색이 슬롯
     토큰으로 치환된 상태) 기준 → "같은 그림 → 같은 id" 유지(colorway 무관).
2. **`MotifParams.colors`**: `{모티프슬롯 id → 팔레트 슬롯 id}`(스키마 존재, `intent.py`). 단색은
   `color`(슬롯 1개 단축형).
3. **`compose()`**(`composition.py`): `colors` 거부(`:111`) 제거. colorway로 각 슬롯을 concrete hex로
   resolve → **인스턴스에 바인딩**(아래 수단). `<symbol>`은 colorway 무관하게 1회 정의·dedup 유지.
4. **슬롯 바인딩 수단 (N2)**: `currentColor`는 인스턴스당 1색뿐이라 N개 불가.
   - **기본 = (b) 슬롯별 `<g>` 분리 + 슬롯 수만큼 `<use>` 겹쳐 각기 `color` 주입.** 이 방식만이
     §4.2-3의 "`<symbol>` 1회 정의·dedup 유지" 불변식과 **양립**한다(symbol은 colorway 무관, 색은
     인스턴스).
   - (a) "슬롯→hex 치환 per-instance 인라인 symbol"은 **dedup 불변식을 깨므로 폐기**(C1의 굽기를
     인스턴스 레벨로 옮긴 것). rsvg/resvg가 (b)에서 문제를 일으킬 때만 폴백으로 고려하되, 그 경우
     §4.3의 dedup 수용기준을 명시적으로 완화한다고 단서. → §12 렌더러 실측으로 (b) 확정 목표.
5. **검증**(`validate/intent.py`): `colors` 키가 motif `color_slots`를 **전부** 덮는지 + 값이 팔레트
   슬롯에 존재하는지. **미바인딩 슬롯 처리 규칙 정의**(거부 or 기본색 — 잔존 `currentColor` 누출 금지).

### 4.3 수용 기준
- 멀티컬러 SVG(슬롯 N개, **N>2 포함**) 등록 → 슬롯 보존, `colors` 매핑으로 각 슬롯이 팔레트 색으로 렌더.
- 미바인딩 슬롯은 정의된 규칙대로(거부 or 기본색) — 잔존 `currentColor`로 새지 않음.
- `<symbol>`은 colorway 무관하게 **id·본문 동일**(dedup 유지). colorway만 바꿔도 symbol 정의 불변,
  인스턴스 색만 변경.
- 같은 intent/seed/colorway → 바이트 동일 SVG(**멀티컬러 회귀 가드 신규**).
- 단색 모티프(`color`)는 기존과 동일(하위호환).

---

## 5. 모티프 라이브러리 (영속화 — D3·D4)

저장소는 **Supabase**다. 모티프 행(메타 + 작은 텍스트 SVG)은 Postgres 테이블에, 의미검색은
`pgvector` 확장(`create extension vector`)으로 처리한다. 원본 대용량 에셋(정규화 전 Recraft
풀해상도 등)을 보관하게 되면 그때 **Supabase Storage**에 두고 DB엔 경로만 둔다.

### 5.1 스키마 (Supabase / PostgreSQL)
```sql
CREATE TABLE motifs (
  id            text PRIMARY KEY,        -- 콘텐츠 해시 (register_motif 반환)
  symbol        text NOT NULL,           -- 정규화된 <symbol> SVG (수백 바이트~수 KB)
  color_slots   jsonb NOT NULL,          -- ["s0","s1",...]  (멀티컬러)
  bbox          jsonb NOT NULL,
  anchor        jsonb NOT NULL,
  -- 의미/큐레이션 메타 (D10: subject·part = 통제 어휘 가드레일 / 나머지 = 자유 텍스트+임베딩)
  subject       text NOT NULL,           -- [통제] pig, pelican, ...  (CHECK or FK to vocab)
  part          text NOT NULL,           -- [통제] whole | face | feet | head | ...
  view          text,                    -- [자유] front, back, side, ... (필요시 통제 승격 후보)
  expression    text,                    -- [자유] smiling, 장난스러운 미소, ...
  style         text,                    -- [자유] flat, line, detailed, ...
  description   text,                    -- [자유] 임베딩 소스 문장
  tags          text[] DEFAULT '{}',     -- [자유] 보조 키워드
  embedding     vector,                  -- pgvector (description/명세 임베딩)
  source        text NOT NULL,           -- 'builtin' | 'llm' | 'recraft'
  status        text NOT NULL DEFAULT 'auto',  -- 'auto' | 'curated'
  quality       real,                    -- 큐레이션 점수(선택)
  variant_group text NOT NULL,           -- 같은 명세의 변형 묶음 키
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON motifs (variant_group);
CREATE INDEX ON motifs (subject, part);
CREATE INDEX ON motifs USING ivfflat (embedding vector_cosine_ops);
```

위 SQL은 기준 정의(reference)다. 실제 DB migration 생성·실행은 React 모노레포가 담당하며,
이 레포는 Supabase migration, `supabase db push/reset`, 임의 DDL을 실행하지 않는다. ivfflat
인덱스도 행 수가 충분해지는 시점에 React 모노레포 migration으로 조율해 추가한다.

**facet 어휘 전략 (D10)**: 객관식/주관식은 *창의성*이 아니라 *검색* 축이다. 창의적 해석은
LLM이 앞단에서 끝내고, facet은 "저장·조회 키"일 뿐이다. 그래서 **주관식(자유 텍스트+임베딩) 위주**로
가되, **정확성 가드레일로 `subject`·`part`만 통제 어휘**로 둔다.

- 이유: 임베딩은 "비슷한 뜻"은 잘 묶지만 "급(granularity)이 다른 것"을 못 가른다. 예) "웃는 돼지
  얼굴" 요청에 "웃는 돼지 전체"가 임베딩상 매우 가까워 **오매칭(얼굴↔몸통)** 될 수 있다. `part`를
  하드 필터로 두면 막힌다.
- `subject`·`part`는 종류가 뻔하고(통제 부담 적음) 큐레이션/통계 쿼리에도 깔끔하다.
- `view`는 경계 사례 — back↔front 오매칭이 문제되면 통제로 승격 후보. 기본은 자유.
- LLM은 `subject`·`part`만 정해진 어휘에 매핑하고, 나머지는 자유롭게 기술한다.

### 5.2 레지스트리 복원
- 부팅 시: `SELECT`로 로드 → `register_motif()`로 인메모리 레지스트리 복원, 또는
- 요청 시 lazy: 필요한 `motif_id`/명세만 조회 후 등록(대형 카탈로그 대비).
- 콘텐츠 해시 id라 중복 INSERT는 멱등(같은 SVG = 같은 id).

### 5.3 수용 기준
- 재시작/다중 인스턴스에서 동일 `motif_id` 조회 성공(인메모리 한계 해소).
- 명세(facet/임베딩)로 검색 가능.

---

## 6. 모티프 해석/획득 오케스트레이션 (글루 — D5·D8·D9)

서비스의 **핵심 신규 작업**. `prompt` 요청에서 모티프를 명세→획득→intent에 주입한다.

> **성격: 전형적 RAG가 아니라 "시맨틱 캐시 + 미스 시 생성".** 검색(임베딩+pgvector)은 RAG와 같은
> 기법이지만, 결과를 LLM에 주입해 답을 grounding하는 게 아니라 **에셋을 직접 재사용**하는 게 목적이다.
> (옵션: 미스 시 유사 모티프를 생성기에 스타일 레퍼런스로 넣으면 RAG화 가능 — 현재 비범위.)

### 6.0 "모티프 명세(descriptor)"란

**SVG 안에 태그를 박는 게 아니다.** SVG는 순수 기하(path)로 두고(정규화가 그 외를 제거),
"이 그림이 *무엇인지*"를 기술하는 **구조화된 메타 레코드**를 SVG와 **별도로** 둔다. 이 명세가
프롬프트와 라이브러리를 잇는 **공통 언어**다.

```jsonc
// 모티프 명세 (DB 행의 facet 컬럼 + tags + embedding 으로 저장)
{
  "subject": "pig", "view": "front", "part": "face",
  "expression": "smiling", "style": "flat",
  "text": "smiling pig face, front view",   // 임베딩 소스
  "tags": ["cute", "baby"]
}
```

같은 명세 모양이 **두 곳**에서 쓰인다:
- **라이브러리(저장 시)**: 모티프 행의 `subject/view/.../tags/embedding` 컬럼 = "이 모티프는
  무엇인가". (시드 모티프는 사람이, 생성 모티프는 생성 단계가 함께 산출해 저장)
- **요청(런타임)**: LLM이 사용자 프롬프트를 **같은 모양**으로 파싱 = "사용자가 원하는 게 무엇인가".

→ 둘을 맞춰 매칭한다. **facet**은 하드 필터(`part='face'`), **embedding**은 퍼지 랭킹
(`happy`≈`smiling`), **tags**는 보조 키워드. 셋을 함께 쓴다.

> 왜 SVG에 안 박나: 정규화가 비기하 노드를 제거하고, SVG 텍스트는 색인·벡터검색이 불가능하다.
> 메타는 DB 컬럼/임베딩에 둬야 필터·유사도 검색이 된다.

### 6.1 단계
1. **명세 추출**: LLM이 프롬프트를 파싱해 intent와 함께 **모티프 명세 리스트**를 구조화 산출
   (subject/view/part/expression/style + 짧은 설명). 한 디자인에 다수 모티프 가능
   (예: 웃는 돼지 얼굴 + 돼지 발자국).
   - **facet 매핑 검증 (리뷰 M2)**: `subject`·`part`는 통제 어휘라 LLM이 **허용 목록 안에서만**
     골라야 한다. 프롬프트에 허용 어휘를 주입하고, 어휘 외 출력 시 1회 재프롬프트(현행
     `llm.build_intent` 재프롬프트 패턴 재사용) 후에도 실패하면 거부/폴백. 매핑 정확도는 수용 기준.
2. **조회 (정확매칭 우선 → 2단계 매칭, D18)**:
   - **(0) 정확매칭** — 정규화 descriptor(통제 facet 부분) **완전 일치** 그룹이 있으면 임베딩 없이
     바로 hit. 콜드스타트/비용 절감 + 모델 호출 회피.
   - **(1) 하드 필터** — 가드레일 facet(`subject`·`part`)으로 후보를 좁힌다. 종류·급(granularity)은
     여기서 정확히 가른다. (단 `part`가 카탈로그에 없으면 **false miss → 생성**으로 자연 폴백.)
   - **(2) 소프트 유사도** — 좁힌 후보 안에서 descriptor 임베딩(OpenAI `text-embedding-3-small`)으로
     최근접 검색. 최고 유사도가 **τ 이상이면 hit**(재사용), 미만이면 miss(생성). τ는 **뉘앙스** 만 판단.
   - 함의: **요청이 구체적일수록** 만족하는 게 드물어 자연히 생성으로 흐른다. 두루뭉술하면 재사용.
   - 시작값: **재사용 우선**(변형 샘플링이 다양성 보장)으로 잡고 모델 확정됐으니 **실측 보정**.
   - 임베딩 가정 주의(M2): "임베딩이 급을 못 가른다"는 추정 → part 하드필터로 보강하되, 실측으로
     하드필터 실효성을 검증(과하면 false miss↑).
3. **분기**:
   - **hit** → 해당 `variant_group`의 **풀에서 시드로 변형 1개 선택**(§7).
   - **miss** → 생성(§6.2) → `normalize_motif_svg` → `register_motif` → DB 영속화
     (`status='auto'`) → 새 `variant_group` 시작.
4. **주입**: 선택/생성된 **concrete `motif_id`를 intent의 해당 motif 레이어에 박음.**
5. 엔진 compose → 후보 N개. 각 후보의 **resolved-intent 스냅샷**(concrete `motif_id` 포함)이 재현
   단위다(§7.3, D17).

> 결정/분기(조회·생성 선택)는 **결정론 코드**가 수행한다. LLM은 명세 추출까지만(D9).

### 6.2 생성 소스 라우팅 (D8)
- **단순·기하적** 명세 → **LLM**이 단순 단색/소수색 SVG 생성(싸고 빠름).
- **정교·회화적** 명세 → **Recraft**(`app/adapters/recraft.py`의 `create_motif`)로 멀티컬러 생성.
- 라우팅 규칙(D11): LLM이 명세에 `complexity(simple|detailed)`를 같이 산출 → `simple→LLM`,
  `detailed 또는 멀티컬러 요구→Recraft`. 기본은 LLM, 명시 오버라이드 가능. (Recraft는 느리고
  비싸므로 단순 도형엔 호출하지 않는다.)
- **Recraft 산출물 적합성 게이트 (리뷰 M1 — FRAGILE 가정)**: Recraft/회화 생성기는 보통
  `gradient`/`filter`/`clipPath`/raster를 섞는데 sanitizer allowlist는 이를 **거부**한다
  (`sanitize.py`). 따라서: ① 받은 SVG를 **path-only로 평탄화**(gradient/filter 제거·근사) 시도,
  ② 색 수 **상한 N슬롯** 초과 시 양자화 또는 거부, ③ sanitize 실패 시 **재생성 1회 → 그래도 실패면
  거부·폴백**. "Recraft가 깨끗한 path-only·≤N색 SVG를 준다"는 **검증 대상 가정**으로 표기, 실측
  수용 기준(샘플 X개 중 Y% 통과)을 둔다.

### 6.3 수용 기준
- "돼지 무늬" 프롬프트 → 미등록이면 생성·등록 후 패턴 합성까지 한 흐름으로 성공.
- 같은 프롬프트+seed 반복 → 동일 결과(아래 §7 결정성).

### 6.4 에러 처리 (리뷰 누락분)
신규 글루(`motif_resolver`)의 실패를 HTTP로 매핑(현행 `generate.py`의 422/502 정책 확장):

| 단계 실패 | 처리 |
|---|---|
| LLM 명세 추출 실패 / facet 어휘 외 (재프롬프트 후도) | 422 (의미 불가) |
| 생성(LLM/Recraft) 실패·미설정 | 502 (업스트림) |
| Tier1 게이트 탈락(sanitize/구조검사) | **재생성 1회 → 그래도 탈락이면** 해당 모티프 후보 드롭. 다른 모티프/후보가 있으면 부분 성공, 전부 실패면 502 |
| DB 영속화 실패 | 모티프는 인메모리로 이번 요청 제공(graceful) + 영속화 비동기 재시도. 영속화 안 됐으면 다음 요청에서 재생성될 수 있음(멱등) |

- **캐시 무효화**: 모티프가 Tier2에서 반려·삭제되면 인메모리 `MOTIFS` + 어댑터 캐시(`_intent_cache`/
  `_motif_cache`)와 DB의 일관성을 맞춰야 한다(삭제 전파). 규칙은 구현 세션에서 확정.

---

## 7. 변형 샘플링 & 결정성 (D6·D7)

### 7.0 variant_group (같은 명세의 동치류 — 리뷰 M4·N1)
- **그룹 키 = 단일 기준(결정론)**: `variant_group = sha256(canonical(subject, part, 핵심 facet))`(D16).
  무엇이 "핵심 facet"인지(통제 facet만? expression 포함?)는 구현 세션에서 못박되, **두 구현이 같게
  나오도록 정규화 규칙을 명시**한다.
- **합류는 키 동일성만으로 판정(τ 사용 안 함, N1)**: miss로 생성한 모티프는 **자기 descriptor의 facet
  키**로 그룹 배정 — 같은 키면 같은 그룹(변형 추가), 다른 키면 신규 그룹. **τ(임베딩 유사도)는 §6.1
  조회(hit/miss)에만** 쓰고 **그룹 배정엔 쓰지 않는다**(이중정의 제거). 즉 "fuzzy 조회로 hit한 모티프를
  재사용"과 "결정론 키로 그룹/풀을 구성"은 별개 단계다.

### 7.1 시드 기반 선택
```text
pool    = sorted(curated 변형들, key=motif_id)         # 풀 내용에 안정적인 정렬
variant = pool[ stable_hash(variant_group + ":" + seed) % len(pool) ]
```
- `stable_hash` = **같은 해시 알고리즘**(sha256 + canonical 직렬화)을 쓴다는 뜻 — `layout_id_for`는
  intent 전체를 해싱하므로 **같은 함수 재사용이 아니라** 같은 방식의 **신규 헬퍼**를 한 곳에 정의해
  모든 호출이 참조(언어/플랫폼 무관 안정).
- 같은 (프롬프트, seed) → 항상 같은 변형(재현). seed만 바꾸면 다른 변형(다양성).
- **랜덤 금지** — 결정성 계약 보존.

### 7.2 후보 팬아웃과 결합 (후속 — 필요 시 구현)
- **목표**: `candidate_count`로 후보를 만들 때 **후보마다 다른 변형**을 뽑게 해 한 요청에 다양한
  시안 제시(`app/engine/candidates.py`의 다양성 축에 "motif variant" 추가).
- **현황**: 미구현. `resolve_motifs()`가 요청당 1회·단일 seed로 `motif_id`를 레이어에 고정한 뒤
  `generate_candidates`가 돌므로, 한 요청의 N개 후보는 모티프 변형을 공유한다(다양성 축은
  symmetry/drop_fraction/seed뿐). 요청 seed를 바꾸면 변형이 바뀌는 **요청-레벨 결정성은 정상** —
  누락은 "한 요청 내 후보 간" 다양성에 한정된다.
- **보류 판단(correctness 아님, 제품/UX 결정)**: 결정성·재현성을 깨는 버그가 아니라 품질 향상
  기능이다. 가치는 §8 안전망 논리에 있다 — Tier1이 의미검사를 안 하므로(명세와 안 맞는 모티프가
  나갈 수 있음) 그 완충재가 "후보 N개"인데, 가장 틀리기 쉬운 축이 모티프 모양 자체라 같은 변형을
  N개 내보내면 그 축에서 완충이 무력화된다.
- **실효 전제**: 풀 ≥2(같은 `variant_group`에 curated 변형 2개 이상)일 때만 의미. 롱테일 온디맨드
  명세는 풀=1 → 자연 degrade(§7.4). S14 시드가 head/인기 명세는 ≥2로 만들어 둠. **head 트래픽
  비중이 의미 있을 때 구현 권장, tail 위주면 §7.4 degrade에 기대 보류.**
- **구현 시 주의**: 단순 축 추가가 아니다. (1) `_candidate_id`가 `layout:colorway:seed`만 해싱하므로
  모티프만 다른 후보끼리 **id가 충돌** — motif_id(또는 resolved-intent 해시)를 접어 넣어야 한다.
  (2) rank_key·dedup·diversity 경고가 layout_id 중심이라 **layout 다양성 vs motif 변형 다양성의
  우선순위 결정**이 필요(후보 N개에 두 축 배분). 재현은 resolved-intent 스냅샷(§7.3) 기반이라
  후보별 intent가 달라져도 깨지지 않는다.

### 7.3 재현성 (풀 성장 대비 — 리뷰 C2·M5, D17)
- ⚠️ 정정: `ReproMeta`/`ReproResponse`에는 **`motif_id` 필드가 없다**(`determinism.py`,
  `schemas/generate.py`엔 `intent_version/engine_version/registry_version/seed/colorway_id/layout_id`만).
  "repro에 motif_id를 핀한다"는 **불가** — 이전 서술은 오류였다.
- **재현 단위는 resolved-intent 스냅샷**: 엔진은 concrete `motif_id`가 박힌 intent만 받으므로,
  `CandidateResponse.intent`(resolved intent)를 저장·재투입하면 풀 변화와 무관하게 정확 재현된다.
- **풀은 가변 전역 상태** → 저장 안 한 일회성 요청은 풀 성장 시 `% len(pool)`이 바뀌어 결과가 달라질
  수 있다. 이를 위해 **풀 변경 시 `registry_version` bump**(이미 `repro`에 기록됨)하여, 같은
  `(prompt, seed, registry_version)`이 동일 결과임을 보장한다.
- **정식 재현 경로 = resolved-intent 스냅샷**(위). `ReproMeta.resolved_motif_ids` 추가는 **비범위**
  (intent 스냅샷으로 이미 닫히므로 불필요한 선택지 — 결정 부담만 늘림). 후속에 명시적 필요가 생기면 그때.

### 7.4 풀 구성 규칙
- **`status='curated'`(승인) 변형만 샘플링 풀에 진입**(품질 바닥). `auto`는 검수 대기.
- 콜드스타트(풀=1)는 자연 degrade(항상 그 1개).

---

## 8. 검수 / 승격 루프 (D5·D14)

**2단계 게이트** — 요청자는 즉시 받고, 공유 풀은 검수본만.

```text
생성 → [Tier1 즉시 사용 게이트 — 전부 자동]
        sanitize(allowlist) + 구조검사 통과 (탈락 시 동작 = §6.4)
        → status='auto', DB 영속화, 요청자에게 즉시 제공 (재현 = resolved-intent 스냅샷, §7.3)
     → [Tier2 라이브러리 승격 — 사람 수동 검수]
        승인 → status='curated' → 샘플링 풀(§7.4) 편입
        반려 → 폐기 또는 수동 보정 후 재등록
```

- **Tier1(자동, 요청 경로 내)**: `sanitize`(이미 강제) + 구조 휴리스틱(drawable 존재, degenerate
  아님, bbox 비율, 렌더 에러 없음, 배치 후 seamless 유지). **비전 LLM 의미검사는 비용상 보류**(D14).
  구현은 `app/motifs/registry.py`의 `normalize_motif_svg`(임계값은 `Settings.motif_*`). "배치 후
  seamless 유지"는 모티프를 **여백 타일로 1회 렌더해 선언 bbox를 벗어나는 overflow를 `edge_seam`으로
  거르는 휴리스틱**이며(렌더러 미설치 시 #4·#5는 graceful skip, 순수 기하 #3은 항상 실행), 실제 타일
  seamless는 엔진 by-construction 보장(대칭 overflow는 edge_seam이 못 잡는 휴리스틱 한계 수용).
- **Tier2(사람, 비동기)**: 사람이 보고 `curated` 승격. 공유 샘플링 풀엔 `curated`만 들어가므로
  품질 바닥이 유지된다.
- 인기 상위(head) 명세는 **미리 고퀄로 시드**(검수 부담↓), 롱테일은 온디맨드 생성 후 검수로 성장.

**감수하는 리스크**: Tier1이 의미검사를 안 하므로, 구조는 멀쩡하나 "명세와 안 맞는"(예: 돼지 같지
않은) 모티프가 요청자에게 갈 수 있다. 완충: 한 요청에 **후보 N개**(`candidate_count`) 제시 +
**재생성** 옵션. 공유 풀에는 검수 전이라 흘러가지 않는다.

---

## 9. 결정성 계약 (불변식 — 회귀 가드)

1. 엔진(`generate`/`compose`)은 **concrete `motif_id` 확정 intent**만 입력받는다.
2. `(intent_version + intent + seed + colorway) → 바이트 동일 SVG`(기존 계약 유지).
3. 변형 선택은 `(variant_group, seed)`의 순수 함수.
4. 비결정 단계(LLM/Recraft/임베딩 검색)는 엔진 경계 밖에서 motif/명세를 **고정(freeze)** 후 투입
   (현행 어댑터 캐시 정책과 동일 — `llm.build_intent`/`recraft.create_motif`의 cache).
5. 저장 디자인의 재현 단위는 **resolved-intent 스냅샷**(repro엔 motif_id 필드 없음 — C2/§7.3). 풀
   변경은 **`registry_version` bump**로 봉인.
6. 멀티컬러 색→슬롯 할당은 **문서 DFS 첫 등장 순**(§4.2), 부동소수는 `units.fmt`(4자리) — 기존 규약 유지.
7. 변형 풀은 **`motif_id` 정렬 + canonical sha256 `stable_hash`**로 선택(§7.1) — 풀 내용 순서에 불변.

---

## 10. 영향 받는 코드 지점

| 순서 | 영역 | 파일 | 변경 |
|---:|---|---|---|
| 1 | 멀티컬러 정규화 | (수정) `app/motifs/registry.py` (`normalize_motif_svg`, `_recolor_to_slot`, `MotifDef`) | 색→슬롯 보존(DFS순), `color_slots` 추가, id=슬롯화 기하 해시 |
| 2 | 멀티컬러 합성 | (수정) `app/engine/composition.py` (compose, `:111` 가드) | `colors` 거부 제거. `<symbol>` colorway-무관 유지 + **인스턴스 색 바인딩**(굽기 X, D15) |
| 3 | 멀티컬러 검증 | (수정) `app/validate/intent.py` | `colors`가 `color_slots` 전부 덮는지 + 팔레트 존재 + 미바인딩 슬롯 규칙 |
| 4 | 모티프 영속화 | (수정) `app/motifs/store.py` + 부팅 훅 | **Supabase** CRUD, 레지스트리 복원, variant_group(D16). resolver보다 먼저 필요 |
| 5 | 임베딩 클라이언트 | (수정) `app/adapters/embedding.py` | OpenAI `text-embedding-3-small` 호출(어댑터, freeze/cache) |
| 6 | 오케스트레이션 글루 | (수정) `app/adapters/motif_resolver.py` | 명세→정확매칭/하드필터/유사도→생성→주입 + 에러 매핑(§6.4) |
| 7 | 명세 추출 | (수정) `app/adapters/llm.py` (`_build_prompt`, `build_intent`) | intent + 모티프 명세 산출 + **facet 어휘 주입·검증**(M2) |
| 8 | 생성 소스 | (수정) `app/adapters/recraft.py` (`create_motif`) | 멀티컬러 활용·라우팅 연결 + **적합성 게이트**(M1) |
| 9 | 변형 샘플링 | (수정) `app/engine/candidates.py` | variant 다양성 축 + resolved-intent 보존 |

모티프 영속화 경계:
- Supabase DSN은 서버 사이드 환경 변수(`SUPABASE_DB_URL`)에만 저장하며 클라이언트 설정·응답·프론트엔드
  번들에 노출하지 않는다.
- `motifs` 테이블 접근은 `app/motifs/store.py`를 통해서만 수행한다. 다른 모듈의 직접 SQL/DSN 접근은
  금지한다.
- §7.3에 따라 `ReproMeta.resolved_motif_ids` 같은 별도 재현 핀은 현 범위 밖이며, 재현 단위는
  resolved-intent 스냅샷이다.

---

## 11. 단계별 실행 순서 (제안)

```text
P0  모티프 DB(Supabase) + 글루 + LLM 단색 생성 + 정확매칭/하드필터 캐시   ← 단색 E2E, 임베딩 없이
      (ivfflat·임베딩 호출 없음. exact descriptor + subject/part 필터만, D18)
P1  임베딩 유사도(OpenAI) + 변형 샘플링(시드) + variant_group(D16)        ← 카탈로그 차오르며 가치↑
P2  멀티컬러 엔진(§4, 슬롯확장 D15) + Recraft 연결(§6.2 게이트)            ← Recraft 가치 실현
P3  Tier2 검수/승격 루프 + head 카탈로그 시드 + (행 수 충분 시 React 모노레포 migration으로) ivfflat 인덱스
```
- **P0**: 현행 단색 엔진으로 끝까지 동작 + 영속화. 임베딩/τ/풀 없이 **정확매칭+하드필터**만(콜드스타트
  dead-code 회피, 리뷰 M3). 모델은 확정됐으나 P0엔 임베딩 호출을 넣지 않는다.
- **P1**: 카탈로그가 어느 정도 차면 임베딩 유사도를 켠다(소량 구간은 seq scan, ivfflat은 P3).
  변형 샘플링은 **풀=curated만**(§7.4)인데 Tier2 승격이 P3라 **P1~P2 동안 풀은 사실상 ≤1개** →
  변형 샘플링은 P1에서 degenerate(코드는 켜되 실효는 P3부터). P1의 가치는 **fuzzy 재사용 조회**이지
  변형 다양성이 아니다(N1/N2 후속 정합 메모).
- **P2**: 멀티컬러는 §4.2 슬롯확장(기본 (b), 굽기 금지)으로 C1 해소 후. Recraft는 §6.2 적합성 게이트
  통과분만. **D1상 멀티컬러는 "필수"지만, 단색 E2E가 더 짧은 가치 경로라 P2로 둔다** — Recraft의
  멀티컬러 가치는 P2에서 실현(N3).

---

## 12. 리스크 / 열린 이슈

**확정(리뷰 반영)**: 라우팅(D11), 모델(D12), 2단계+정확매칭(D13·D18), 검수(D14), 멀티컬러 방식
(D15 슬롯확장), variant_group(D16), 재현(D17 intent 스냅샷+registry_version).

**남은 열린 항목**:
- **슬롯 바인딩 렌더러 호환 (C1 후속)**: §4.2-4의 (a)per-instance 인라인 vs (b)슬롯별 `<use>` 겹침 중
  rsvg/resvg에서 동작·출력크기 검증 후 택1. CSS `var()` 지원 여부 실측.
- **Recraft 적합성 실측 (M1)**: 샘플 SVG가 sanitize 통과율·평탄화 가능성·색 수 분포. 색 상한 N 결정.
- **τ 절대값**: 전략 확정(D13). 모델(OpenAI 3-small) 확정됐으니 **소량 라벨셋으로 실측 보정**.
- **통제 어휘 목록**: `subject`·`part`의 **실제 허용값** 확정 + LLM 매핑 가이드/검증(M2).
- **Tier1 구조 휴리스틱 기준값**: 구현 완료(`normalize_motif_svg`). 초기값 — bbox 비율 `max:min ≤ 20`
  (`motif_max_aspect_ratio`), seam `edge_seam ≤ 2.0`(`motif_edge_seam_tol`, `00-overview`와 정합), 렌더러
  부재 시 graceful skip(`motif_render_check`로 일괄 토글). **실제 모티프로 임계 보정은 후속**(비율/seam 튜닝).
- **variant_group "핵심 facet" 범위**: 통제 facet만 vs expression 포함 — 그룹 입자 결정(D16 구체화).
- **캐시 무효화 규칙 (§6.4)**: Tier2 반려·삭제 시 인메모리/어댑터 캐시/DB 일관성 전파.
