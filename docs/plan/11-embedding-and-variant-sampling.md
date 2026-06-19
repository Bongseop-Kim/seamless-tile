# 세션 11 — 임베딩 유사도 + 변형 샘플링 [P1]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/motif-library-and-multicolor.md` — §6.1(2단계 매칭), §7(변형 샘플링·결정성),
  §7.0(variant_group), 결정 **D6·D7·D12·D13·D16·D18**, §9(결정성 불변식).
- **현재 코드**: `app/adapters/motif_resolver.py`(S10 산출), `app/engine/candidates.py`(다양성/랭킹),
  `app/engine/determinism.py`(`layout_id_for` = sha256+canonical, `ReproMeta`, `registry_version`).
- **선행 세션**: **S9, S10**.

## 목표
정확매칭 miss 시 **임베딩(OpenAI `text-embedding-3-small`) 2단계 매칭(τ)**과 **시드 기반 변형 샘플링**을
추가한다. 풀 성장에 따른 재현성을 `registry_version`으로 봉인한다.

## 선행 조건
S9(영속화), S10(글루·단색 E2E).

## 범위
- (신규) `app/adapters/embedding.py`: OpenAI `text-embedding-3-small` 호출(어댑터, freeze/cache).
- descriptor 임베딩 저장·검색 — **소량 구간 seq scan**(ivfflat은 S14, D18).
- 2단계 조회: 하드필터 후 **τ 이상 hit / 미만 miss**. τ는 뉘앙스만 판단.
- 시드 변형 선택(§7.1): `pool = sorted(curated, key=motif_id)`,
  `variant = pool[stable_hash(variant_group+":"+seed) % len(pool)]`. `stable_hash`는 sha256+canonical
  **신규 헬퍼**(=같은 알고리즘, `layout_id_for`와 같은 함수 아님).
- 후보 팬아웃: 후보마다 다른 변형(`candidates.py` 다양성 축).
- 풀 변경 시 **`registry_version` bump**(§7.3, M5).

## 비범위
ivfflat 인덱스(S14), Tier2 curated 승격(S14). **주의**: 그 전까지 풀은 ≤1(degenerate)이라 변형
샘플링 코드는 켜되 **실효는 S14부터**. 멀티컬러(S12).

## 작업 / 만들·수정 파일
- (신규) `app/adapters/embedding.py`.
- `app/adapters/motif_resolver.py`(2단계 추가).
- `app/engine/candidates.py`(변형 축).
- `app/engine/determinism.py`(`registry_version` 규칙, `stable_hash` 헬퍼).

## 수용 기준 (검증 가능)
1. τ 이상 → 재사용, 미만 → 생성(단위 테스트로 경계 검증).
2. 같은 `prompt + seed + registry_version` → 동일 결과.
3. 풀 ≥ 2일 때 seed만 바꾸면 다른 변형이 선택됨.
4. 변형 선택이 풀의 DB 조회 순서에 **불변**(정렬 기준 고정).

## 리스크
τ 절대값 실측(모델 확정됨 → 소량 라벨셋 보정), 콜드스타트 풀 공백, 임베딩 호출 비용/지연.

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + verifier. 임베딩 검색은 엔진 경계 밖 freeze 후 투입.
