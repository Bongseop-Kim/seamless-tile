# 세션 17 — 세션 영속 + 복원 API + 결정론 비용 가드 [P1]

> **단독 실행 단위.** 새 대화창에서 착수하려면 아래 "부트스트랩"을 먼저 읽어라.

## 시작 전 읽기 (부트스트랩)
- **스펙**: `docs/spec/conversational-design-sessions.md` — §8.5(비용 가드 S13), §10(API), §11(영속화 S7),
  §14(수용 기준: 복원·degrade), 결정 **S7·S13**.
- **현재 코드**: 세션 16 산출물(`app/sessions/*`, in-memory checkpointer), `app/motifs/store.py`(SUPABASE_DB_URL client 패턴),
  `app/logs/generation_log.py`(no-op degrade 동형), `app/core/config.py`.
- **선행 세션**: **세션 16(P0)**.
- **⚠ 스키마 규칙(CLAUDE.md)**: 이 레포는 **DDL 금지·마이그레이션 금지**. `design_sessions` 등 필요 테이블은
  **React 모노레포(YeongSeon)가 정의**하고, 이 레포는 `SUPABASE_DB_URL`로 **client 연결만** 한다. LangGraph
  `PostgresSaver.setup()` **호출 금지**(setup이 DDL을 실행) — 테이블은 모노레포 마이그레이션으로 선정의(S14).

## 목표
세션 상태를 `SUPABASE_DB_URL`로 영속화하고 재시작 후 복원한다(미설정 시 in-memory degrade). LangGraph checkpointer
백엔드를 여기에 매핑한다. LLM과 무관한 **결정론 비용 가드**(세션당 Recraft 예산·레이트리밋·in-flight lock·dedup)를 건다.

## 모노레포 선행 (하드 블로커) — 정의해야 할 테이블
**Postgres 영속 경로는 아래 테이블이 모노레포(YeongSeon)에 반영되기 전에는 켜지 않는다.** 그 전까지는
in-memory 경로만 허용(선택이 아니라 순서 제약). 이 레포는 `SUPABASE_DB_URL`로 **client 연결만** 하며
`setup()`/DDL을 절대 실행하지 않는다(CLAUDE.md·S14).

모노레포가 한 마이그레이션에 만들어야 할 테이블(= LangGraph `PostgresSaver.setup()`가 평소 만드는 4개 + 앱 테이블):

| 테이블 | 소유/출처 | 용도 |
|---|---|---|
| `checkpoint_migrations` | LangGraph | 체크포인터 **자체 스키마 버전 원장**(`v INTEGER PK`). Supabase의 `supabase_migrations.schema_migrations`와 **다른 것** |
| `checkpoints` | LangGraph | 턴별 그래프 스냅샷. PK `(thread_id, checkpoint_ns, checkpoint_id)` |
| `checkpoint_blobs` | LangGraph | 채널 값 blob(버전별 out-of-line). PK `(thread_id, checkpoint_ns, channel, version)` |
| `checkpoint_writes` | LangGraph | 태스크별 pending write. PK `(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)` |
| `design_sessions` | **앱 정의**(LangGraph 무관) | 세션 상태(대화·`current_intent`·seed·colorway·status 등). `thread_id`로 위 checkpoints와 연결 |

컬럼/DDL 진실의 출처는 **핀한 `langgraph-checkpoint-postgres` 버전의 `MIGRATIONS` 리스트**다(여기 복사하면 rot). 모노레포는 그 버전을 그대로 미러링하고, 다음 함정을 지킨다:

- **버전 핀 필수**: `MIGRATIONS`는 릴리스마다 늘어난다(예: `checkpoint_writes.task_path` 컬럼, thread_id 인덱스 추가). `requirements.txt`에서 `langgraph`·`langgraph-checkpoint-postgres`를 **정확한 버전으로 핀**하고 모노레포 마이그레이션은 **그 버전의 전체 `MIGRATIONS`**를 반영. 어긋나면 client가 쿼리 시점에 에러(예: 없는 `task_path`)나고 `setup()` 금지라 self-heal 불가.
- **`CREATE INDEX CONCURRENTLY` 금지**: `setup()`는 thread_id 인덱스를 `CONCURRENTLY`로 만드는데, 이는 트랜잭션 안에서 못 돈다. Supabase 마이그레이션은 트랜잭션이 기본 → **`CONCURRENTLY` 빼고 평범한 `CREATE INDEX`**(빈 테이블이라 동등).
- **`checkpoint_migrations` 프리시드**: `setup()`가 넣었을 버전 행(`v = 0..len-1`)을 마이그레이션에서 미리 채워, 실행 중인 client가 버전 불일치를 보지 않게 한다.

## 범위
- **영속(S7)**: `SUPABASE_DB_URL` 있으면 세션 상태 + LangGraph checkpoint를 Postgres에 저장, 없으면 in-memory(세션 16 동형).
  - **client 전용**: 앱은 DDL 실행 안 함. `setup()` 미호출 — 테이블은 모노레포가 선정의(부트스트랩 ⚠ 참고).
  - `SUPABASE_DB_URL`은 서버 사이드 전용·RLS 우회 — 클라이언트 노출 금지.
- **복원 API**: `GET /api/v1/sessions/{id}` — 대화 이력 + `current_intent`(노출 정책 §16) + 후보 이력 복원.
- **결정론 비용 가드(S13)**: `budget`(recraft_used/repaint_used…) 카운터를 세션 상태에 두고,
  - 세션당 Recraft 생성·실사화 횟수 **상한**(초과 시 확인에도 거부/경고 — resource-ceiling 정신).
  - **in-flight lock**: 같은 motif spec/승인 타일이 생성 중이면 중복 호출 금지(dedup).
  - **비용/시간 힌트**를 확인 UI에 노출(지출은 항상 사용자가 본 뒤 누른다).

## 비범위
- 스키마/마이그레이션 생성(모노레포 소유 — 이 레포 금지). 모노레포가 위 테이블을 반영하기 전에는 **Postgres 경로를 켜지 않고 in-memory로 P1을 닫는다**(연결은 테이블 반영 후).
- 분기/undo/redo/fork(checkpoint 노출) → **세션 18(P2)**. 여기선 저장·복원·가드까지.
- "자동 승인 + 예산 상한" 완전 자동 모드 — 옵션(기본은 확인 게이트). 필요 시 후속.

## 작업 / 만들·수정 파일
- `app/sessions/store.py`(신규): 세션 상태 저장/조회 — `app/motifs/store.py`의 SUPABASE_DB_URL client 패턴 재사용, 미설정 no-op degrade.
- `app/sessions/checkpointer.py`(신규): LangGraph checkpointer 백엔드 매핑(Postgres client / in-memory), **`setup()` 미호출**.
- `app/sessions/budget.py`(신규 또는 상태 내): 예산 카운터·레이트리밋·in-flight lock·dedup(순수/결정론).
- `app/api/routes/sessions.py`: `GET /sessions/{id}` 복원 추가.
- (모노레포 선행) 위 "모노레포 선행" 표의 5개 테이블 마이그레이션 — 이 레포는 착수 못 함(협의·전달만).

## 수용 기준 (검증 가능)
1. **세션 복원**: `GET /sessions/{id}`로 복원한 intent 재합성이 직전과 **byte 동일**.
2. **재시작 후 재현**: 프로세스 재시작 후에도 세션 상태·`current_intent` 복원·재현.
3. **예산 가드**: 세션 예산 초과 시 추가 생성/실사화가 **확인에도 거부/경고**.
4. **dedup**: 같은 spec/타일 in-flight 중복 호출이 lock으로 차단.
5. **degrade(핵심)**: `SUPABASE_DB_URL` 미설정에서도 세션이 in-memory 동작(에러 없이), 앱이 DDL을 실행하지 않음.
6. **client-only 봉인**: 코드 경로에 `setup()`/DDL/`db push`가 없음 — 회귀 가드.
7. **테이블 부재 시 clean-fail**: `SUPABASE_DB_URL`은 있는데 테이블이 없으면 앱이 **명확한 client 에러**를 내고 **스스로 생성하지 않음**(self-provision 금지) — 테스트로 봉인.

## 리스크
- **스키마 소유권 충돌 방지**: `setup()`·DDL 절대 금지. 모노레포 테이블 반영 전에는 Postgres 경로 미가동(in-memory만).
- **버전 정합**: LangGraph checkpointer 버전 ↔ 모노레포 마이그레이션의 `MIGRATIONS` 스냅샷이 어긋나면 client 쿼리 에러(self-heal 불가) — 버전 핀 + 사전 협의 필수(위 "모노레포 선행" 참고).
- 예산 상한 수치·자동 승인 모드 제공 여부 — §16 열린 결정.

## 종료 조건 (공통)
`pytest` 그린 + 수용 기준 + verifier. `SUPABASE_DB_URL` 미설정 시 in-memory로도 그린.
