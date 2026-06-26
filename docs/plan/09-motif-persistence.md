# 세션 9 — 모티프 영속화 (Supabase store) [P0]

> **단독 실행 단위.** 새 대화창에서 이 파일 하나로 착수 가능하도록 아래 "부트스트랩"을 먼저 읽어라.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/motif-library-and-multicolor.md` — §5(영속화), §7.0(variant_group), 결정 **D4·D16**.
- **아키텍처/규약**: `ARCHITECTURE.md`, `docs/plan/00-overview.md`(결정성 계약).
- **현재 코드**: `app/motifs/registry.py`(`MOTIFS` 전역 dict, `register_motif`, `get_motif`, `MotifDef`,
  `normalize_motif_svg`), `app/core/config.py`(`registry_version`), `app/main.py`(부팅).
- **선행 세션**: 없음.

## 목표
인메모리 전역 `MOTIFS` 레지스트리를 **Supabase**로 영속화한다. 부팅/lazy 복원으로 재시작·다중
인스턴스에서 동일 `motif_id` 조회가 보장되게 한다. 결정론적 `variant_group` 키 헬퍼를 도입한다.

## 선행 조건
없음(현행 단색 엔진·레지스트리 위에 얹는다).

## 범위
- `motifs` 테이블(스펙 §5.1) — 단 `embedding` 컬럼은 **nullable·미사용**(검색은 S11).
- (신규) `app/motifs/store.py`: CRUD. `register_motif`/`get_motif`가 DB 경유(인메모리는 캐시 계층).
- 부팅 시 복원 또는 요청 시 lazy 로드(대형 카탈로그 대비).
- content-hash `id` 기반 **멱등 INSERT**(같은 SVG = 같은 행).
- 결정론 `variant_group` 키 헬퍼(D16, §7.0): `sha256(canonical(subject, part, 핵심 facet))`.
- `subject`·`part` 통제 어휘 스켈레톤(컬럼 + 기본 검증).

## 비범위
임베딩/pgvector 검색(S11), 멀티컬러(S12), Recraft(S13), head 카탈로그/풀 성숙(S14).

## 작업 / 만들·수정 파일
- (신규) `app/motifs/store.py`, 마이그레이션 SQL(Supabase).
- `app/motifs/registry.py` — DB 연동(조회/등록 경로).
- `app/core/config.py` — Supabase 접속 설정.
- `app/main.py` — 부팅 복원 훅.

## 수용 기준 (검증 가능)
1. 재시작/별도 프로세스에서 동일 `motif_id` 조회 성공(인메모리 한계 해소).
2. 같은 SVG 재등록 = 멱등(중복 행 생성 안 됨).
3. 기존 결정성 회귀(`tests/test_determinism.py`) 그린 유지.
4. `variant_group` 헬퍼: 동일 입력 → 동일 키(단위 테스트), 정규화 규칙 명시.

## 리스크
Supabase 접속/마이그레이션 운영, 인메모리↔DB 일관성, 부팅 시 대량 로드 비용.

## 종료 조건 (공통)
`pytest` 그린 + 위 수용 기준 충족 + 작성/검토 분리 verifier 패스. 외부 의존(Supabase)은 어댑터 계층,
미설정 시 명확한 에러/스킵.
