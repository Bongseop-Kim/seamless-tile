# 세션 19 — 모티프 재사용 검색 품질: 라벨셋 · τ 보정 · recall/precision [품질·오프라인]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.
> 수직 슬라이스가 아니라 **오프라인 평가 하니스** — 런타임 경로를 안 바꾸고 τ 기본값과 재사용 품질을 실측한다.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/motif-library-and-multicolor.md` — §6.1(exact→scope→embedding τ 캐스케이드), §12(열린 항목:
  **"τ 절대값 … 소량 라벨셋으로 실측 보정"**), 결정 **D13·D18**.
- **현재 코드**: `app/adapters/motif_resolver.py`(`_resolve_one`의 τ 게이트, `_log_path`가 이미 `path/similarity/selected_id`를
  **τ 보정용으로 로깅**), `app/motifs/store.py`(`find_best_by_embedding` = cosine top-1), `app/adapters/embedding.py`
  (`text-embedding-3-small`), `app/core/config.py`(`motif_similarity_tau` 기본 **0.60 — 미보정 추측값**).
- **선행 세션**: **S11**(임베딩·τ·변형 풀). ivfflat 무회귀는 **S14**와 물린다.

## 문제
τ=0.60은 실측 없는 시작값이고, 재사용 검색의 **precision/recall이 어디서도 측정되지 않는다**. 두 오분류가 곧 비용/품질이다:
- **잘못된 재사용**(false reuse): should-generate인데 τ를 넘겨 엉뚱한 모티프 재사용 → 사용자에 부적합 결과.
- **놓친 재사용**(missed reuse): should-reuse인데 τ 미만으로 판정 → 불필요한 Recraft 지출(§8 비용).

Tier1 게이트가 비전 의미검사를 안 하므로(§8), τ가 사실상 유일한 의미 컷오프다 → 근거 있는 값이어야 한다.

## 목표
소량 **라벨셋**으로 τ를 스윕해 precision/recall/generate-rate 곡선을 만들고 **기본 τ를 근거값으로 확정**한다. ivfflat 도입 시
seq-scan 대비 recall 무회귀(S14 AC#4)를 이 하니스로 실측한다. **런타임 결정론·엔진 경계는 불변**(오프라인 스크립트/테스트만).

## 범위
- **라벨셋**(오프라인 픽스처): `(descriptor spec, 기대 결과)` 페어 N개. 기대 결과 = 재사용 대상 `motif_id`(또는 `"generate"`).
  cold-start를 대변하는 소규모 + head 명세 중심. `tests/fixtures/motif_eval/`.
- **τ sweep**: 라벨셋에 대해 τ를 스윕 → precision/recall/generate-rate/false-reuse-rate 리포트 → 기본 τ 선정 → `config.py` 반영.
- **recall 무회귀**: ivfflat vs seq-scan 검색 결과가 라벨셋에서 동등 이상(S14 AC#4를 이 하니스로 봉인).
- 기존 `log_metrics("motif_resolve", …)`의 `similarity`/`path`를 재활용 — 신규 계측 최소화.

## 비범위
- 비전 LLM 의미검사(Tier1이 의도적으로 안 하는 것), 온라인 A/B, 리랭커/MMR(사용자가 top-K 리랭킹 — 인터랙티브 경로).
- 라이브 트래픽 자동 라벨링, CI 게이팅(초기엔 수동 실행 리포트). chunking/keyword-hybrid(이 코퍼스엔 무의미).

## 작업 / 만들·수정 파일
- `scripts/eval_motif_retrieval.py`(신규): 라벨셋 로드 → 각 spec resolve(τ sweep) → precision/recall/generate-rate 리포트.
- `tests/fixtures/motif_eval/*.json`(신규): 라벨셋.
- `app/core/config.py`: `motif_similarity_tau` 기본값을 sweep 결과로 보정(코드가 아니라 **값** 변경).
- (선택) `tests/test_retrieval_eval.py`: 라벨셋에서 precision/recall ≥ 기준선 스모크(결정론 픽스처).

## 수용 기준 (검증 가능)
1. **τ 근거화**: sweep 곡선 산출 + 기본 τ가 라벨셋 근거로 설정(0.60 추측값 대체).
2. **품질 리포트**: 라벨셋에 대한 precision/recall/generate-rate/false-reuse-rate 리포트가 재현 가능.
3. **recall 무회귀**: ivfflat 도입 후 라벨셋 recall ≥ seq-scan 베이스라인(S14 AC#4 실측).
4. **경계 불변**: 런타임 결정론·엔진 경로 무변경(스크립트/픽스처만; `pytest -q` 그린, `test_determinism` 통과).

## 리스크
- 라벨셋 대표성·크기(소량이라 τ 과적합 위험 — 곡선으로 판단하고 보수적 선택).
- **임베딩 모델 버전 핀**: `text-embedding-3-small`이 바뀌면 τ 재보정 필요(결정론 입력의 일부).

## 참고 — 별도로 안 만든 것 (요청 목록 중)
문서-RAG 체크리스트 대부분은 이 "semantic cache" 시스템에 해당 없음: **chunking**(원자 descriptor, 쪼갤 문서 없음),
**keyword hybrid**(이미 exact+scope+vector 캐스케이드; 몇 단어 코퍼스에 lexical 레그 이득 ≈0), **reranking**(top-1 +
사용자 top-K 픽; 리랭커는 결정론·비용 손해), **RAG식 citation**(출력이 SVG). **metadata filter**(`scope`)·**검색 실패 처리**
(τ + fallback + 인터랙티브 게이트)·**trace/logging**(`motif_resolve` 라인)은 **이미 구현/계획됨**. (작은 후속 여지:
세션/finalize 응답에 모티프 **재사용 vs 신규생성 + `motif_id`/`source`** 노출 — IP 가시성용, 소규모.)

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + 작성/검토 분리 verifier. 스크립트는 오프라인(외부 호출은 임베딩 API — 미설정 시 skip).
