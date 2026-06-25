# 세션 14 — 카탈로그 + 풀 성숙 [P3]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/motif-library-and-multicolor.md` — §7.4(풀 구성), §8(Tier1 게이트/즉시 재사용),
  §6.4(캐시 무효화), 결정 **D5·D14·D18**.
- **현재 코드**: `app/motifs/store.py`(MotifStore), `app/adapters/motif_resolver.py`(변형 풀),
  `app/adapters/llm.py`/`recraft.py`(어댑터 캐시 `_intent_cache`/`_motif_cache`).
- **선행 세션**: **S9**(영속화), **S11**(변형 샘플링·풀).

## 목표
head 카탈로그를 시드해 인기 명세의 변형 풀을 빠르게 키우고, 행 수가 충분해지면 ivfflat 인덱스를 도입한다.
Tier1 통과 motif는 저장 즉시 재사용 풀에 들어간다.

## 범위
- **head 명세 고퀄 시드**로 풀 ≥2인 인기 명세 확보.
- 샘플링 풀 = 같은 `variant_group`의 reusable rows 전체.
- **admin delete 경로**: 문제 motif를 DB/인메모리/어댑터 캐시에서 일관 제거.
- **ivfflat 인덱스 도입**(행 수 충분 시, D18) — 소량 구간 seq scan에서 전환.

## 비범위
사람 검수/승격 큐, 자동 미적 평가, 비전 LLM 의미검사.

## 작업 / 만들·수정 파일
- `scripts/seed_head_catalog.py`(head 카탈로그 시드).
- `app/motifs/store.py` / `app/adapters/motif_resolver.py`(풀 조회 계약 유지).
- React 모노레포 migration(ivfflat 인덱스, 필요 시).

## 수용 기준 (검증 가능)
1. 등록된 motif가 즉시 해당 `variant_group` 샘플링 풀에 들어간다.
2. 풀 ≥2에서 같은 명세 반복 요청이 seed별로 다른 변형을 선택한다.
3. 삭제된 motif가 인메모리/어댑터 캐시/DB에서 일관 제거된다.
4. ivfflat 도입 후 검색 recall 회귀 없음(seq scan 대비 동등 이상).

## 리스크
저품질 motif 즉시 재사용, 캐시 무효화 일관성, ivfflat `lists` 튜닝/소량 recall.

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + 작성/검토 분리 verifier.
