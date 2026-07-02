# 실행 플랜 — AI Seamless SVG 생성기

`ARCHITECTURE.md`를 구현하기 위한 **세션 단위 실행 계획**이다. 각 세션은 독립적으로 검증
가능한 체크포인트 하나(수직 슬라이스 또는 한 서브시스템)를 만든다. 한 세션 = 스코프·수용
기준·롤백 경계가 닫히는 한 작업 호흡.

## 왜 세션을 분리하나

작업량이 커서 단일 문서/세션으로 진행하면 중간 검증 지점이 사라지고 컨텍스트가 흐려진다.
세션 단위로 끊으면 각 단계 끝에서 `pytest` 그린 + 수용 기준 충족으로 멈출 수 있고, 다음
세션은 이전 산출물 위에 안전하게 쌓인다. **세션 4에서 MVP가 닫힌다.**

## 의존 순서

```text
1 ──> 2 ──> 3 ──> 4 (MVP 체크포인트)
                  │
                  ├──> 5  (커버리지·대칭)
                  ├──> 6  (제품 API)
                  │      └──> 7  (LLM·이미지 어댑터)
                  └──> 8  (보안·Recraft)   # 3·4·6·7 의존

# 상위 기능층 (spec: motif-library-and-multicolor)
9 (영속화) ──> 10 (글루·단색 E2E) ──> 11 (임베딩·변형) ──> 14 (카탈로그·풀 성숙)
                     └──> 13 (Recraft) ── 의존 ──> 12 (멀티컬러 엔진, 엔진 독립)
# P0=9+10, P1=11, P2=12+13, P3=14

# 파생출력 & 대화형 스튜디오 (spec: photoreal-fabric-render, conversational-design-sessions)
15 (실사화 finalize, 독립) ──(P0 실사화 버튼이 핸드오프)──┐
16 (세션·edit-as-delta·비용 게이트 P0) ──> 17 (영속·복원·비용 가드 P1) ──> 18 (fork/undo P2)
# 15는 stateless 위에서 단독 완결. 16이 전환점. LangSmith(P3)·planner/critic는 선택/후보 — 별도 착수.
```

7은 6에, 8은 3·4·6·7에 의존한다. 6의 "placement 종류" 다양성 축은 5를 전제하므로, 5 없이 4 직후
6을 시작하면 다양성은 colorway·seed·layout 변형으로 제한된다.

## 세션 목록

| # | 문서 | 목표 | 체크포인트 |
|---|---|---|---|
| 1 | `01-intent-contract-and-color.md` | intent 계약·검증·색 모델 | 픽스처 intent 검증/복구 + 결정론 해석 |
| 2 | `02-primitives-and-host-contract.md` | background·stripe·lanes()·각도 스냅 | stripe geometry + lane 계약 + commensurate 각도 |
| 3 | `03-placement-composition-vertical-slice.md` | path_following·`<pattern>` 합성·motif registry | MVP intent가 `<pattern>`+`<use>` SVG로 합성 |
| 4 | `04-seamless-and-generate-mvp.md` | seamless 강제·검증·generate 파이프라인 | **MVP: 사선 타이 seamless SVG + 결정론** |
| 5 | `05-coverage-and-scatter.md` | lattice/scatter/point_set·sateen | 비사선 계열 생성(과적합 반증) |
| 6 | `06-product-api-and-ops.md` | `/api/v1/generate`·다양성/랭킹·에러/관측 | intent 직접 경로로 후보 반환·de-dup·랭킹 |
| 7 | `07-llm-and-reference-image-adapters.md` | prompt→intent, image→intent | 모킹 어댑터로 prompt/이미지→SVG |
| 8 | `08-security-hardening-and-recraft.md` | SVG sanitize·업로드 검증·Recraft intake | injection 차단 + Recraft motif 결정론 등록 |
| 9 | `09-motif-persistence.md` | 모티프 Supabase 영속화 + 복원 | 재시작 후 동일 motif_id 조회 |
| 10 | `10-motif-orchestration-glue.md` | prompt→명세→정확매칭→LLM 단색 생성→주입 | "돼지 무늬" E2E + 결정성 |
| 11 | `11-embedding-and-variant-sampling.md` | 임베딩 τ 조회 + 시드 변형 샘플링 | seed별 변형 + registry_version 봉인 |
| 12 | `12-multicolor-engine.md` | 멀티컬러 엔진(슬롯 보존·(b) 바인딩) | N>2 슬롯 렌더 + dedup 유지 + 바이트동일 |
| 13 | `13-recraft-and-routing.md` | Recraft 연결 + 적합성 게이트 + 라우팅 | detailed 명세 → 멀티컬러 합성 |
| 14 | `14-catalog-and-pool.md` | head 카탈로그 시드 + 풀 성숙 + ivfflat | 즉시 재사용 풀 + 변형 실효 |
| 15 | `15-fabric-texture-render.md` | 결정론 원단 텍스처 렌더 + finalize(무료·로컬) | 승인 SVG → 천 느낌 PNG, 결정론·seamless·영역별 질감 |
| 16 | `16-conversational-sessions-p0.md` | 세션 + edit-as-delta(도구) + Recraft 확인 게이트 | 턴2 국소 편집 + 게이트 없이 Recraft/실사화 미호출 + 결정론 그린 |
| 17 | `17-session-persistence-and-cost-guard.md` | 세션 영속·복원 API + 결정론 비용 가드 | 재시작 후 복원·재현, 예산 상한·dedup, in-memory degrade |
| 18 | `18-fork-undo-time-travel.md` | 분기/undo/redo/fork(checkpoint) | 되감기·fork 후보, 재합성 byte 동일 |
| 19 | `19-retrieval-eval-and-tau-calibration.md` | 모티프 재사용 검색 품질: 라벨셋·τ 보정·recall/precision(오프라인) | τ 근거값 확정 + precision/recall 리포트 |
| 20 | `20-review-followups.md` | 세션/finalize 후속 리뷰 반영 | set_material→finalize, dead-code/doc drift 정리 |

세션 9–14는 상위 기능층(멀티컬러 모티프 라이브러리 & 프롬프트 생성)으로,
설계 기준은 `docs/spec/motif-library-and-multicolor.md`다. **세션 15**의 기준은
`docs/spec/photoreal-fabric-render.md`(파생출력, stateless 위에서 독립), **세션 16–20**은
`docs/spec/conversational-design-sessions.md`(대화형 스튜디오, LangGraph)다. LangSmith 트레이싱(P3)과
planner/critic 멀티에이전트는 스펙상 **선택·후보** — 별도 착수(문서 미생성). **세션 17의 Postgres 영속은
client-only** — `design_sessions` + LangGraph 체크포인트 4개 테이블은 **모노레포가 정의**하고 이 레포는
`setup()`/DDL을 실행하지 않는다(모티프 밴드와 동일 규칙, 세션 17 "모노레포 선행" 표 참고). 각 세션 파일은
**새 대화창에서 단독 실행** 가능하도록 "부트스트랩"(읽을 스펙/코드)을 자체 포함한다.

## 공통 규약 (모든 세션 적용)

- **스택**: Python 3.x, FastAPI, Pillow. 외부 의존(LLM·Recraft·저장소)은 코어 밖 어댑터.
- **파일 경로**: 모든 경로는 `app/` 패키지 루트 기준이다 — `engine/intent.py` =
  `app/engine/intent.py`, `validate/seamless.py` = `app/validate/seamless.py`. 새 최상위
  패키지를 만들지 않는다.
- **테스트**: `.venv/bin/python -m pytest -q`. 각 세션은 자기 수용 기준을 검증하는 테스트를 추가한다.
- **단위**: 내부 mm, `px = round(mm/25.4*dpi)`는 래스터 경계에서만.
- **결정론(필수 회귀 테스트)**: 같은 `intent_version`+intent+seed+colorway → **바이트 동일 SVG**.
  비결정 단계(LLM·이미지·Recraft)는 authoring 경계 밖에서 intent/motif를 고정한 뒤 파이프라인에 넣는다.
  부동소수는 `units.fmt`(소수 4자리)로 직렬화하고, scatter 좌표 생성 순서를 seed로 고정한다.
- **seamless 수용**: by-construction 불변식이 1차 보증, `edge_seam`은 회귀 가드. **초기 임계값
  `edge_seam ≤ 2.0`(per-channel mean, 핀 렌더러·300dpi)** 를 골든 베이스라인으로 잡고 첫 실행에서
  보정·고정한다. 검증 렌더러·버전·DPI를 핀으로 고정.
- **commensurability·각도 스냅 정책(단일 출처)**: 모든 세션이 동일 규칙을 쓴다 — 타일 정합 조건은
  `period_mm | tile_mm`, `lane_spacing × k = tile_period`(정수해), 사선 각도는 `snap_angle()`이
  연분수 근사로 타일 격자의 유리 기울기 `p/q`에 최근접 스냅. 세션 1(스냅 가능성 판정)·2(스냅
  구현)·4(강제)는 이 정의를 **참조만** 하고 재정의하지 않는다.
- **출력**: `<pattern patternUnits="userSpaceOnUse">` + `<symbol>`/`<use>`. **인스턴스 enumerate
  금지** — 출력에 `<pattern>`이 쓰이는지 회귀 가드 테스트로 봉인.
- **세션 종료 조건**: `pytest` 그린 + 해당 세션 수용 기준 충족 + 별도 verifier 패스(작성/검토 분리).

## 기존 토대 (재사용·리팩터 대상)

- `engine/units.py` — mm/px·숫자 포맷 (재사용; `snap_angle` 추가)
- `engine/palette.py` — hex 검증·`Colorway` 인덱싱 (→ 슬롯/colorway 모델로 확장)
- `engine/placement/repeat.py` — block/half_drop/brick (→ 세션 5에서 `lattice`로 흡수)
- `render/svg.py` — mm SVG 문서 (→ 세션 2에서 출력 인코딩 추가)
- `render/raster.py` — rsvg/resvg + DPI (재사용; 검증 렌더러 핀)
- `validate/seamless.py` — `edge_seam`/`tiling_seam` (재사용; 회귀 가드로 승격)

## 세션 브리프 템플릿

각 세션 문서는 다음을 따른다: **목표 · 선행 조건 · 범위 · 비범위 · 작업 · 만들/수정 파일 ·
수용 기준 · 리스크**.
