# AGENTS.md

seamless-tile — AI seamless SVG 생성 서비스 (Python / FastAPI).
설계 기준은 [ARCHITECTURE.md](ARCHITECTURE.md), 개요는 [README.md](README.md).

## 프로젝트 개요

- 엔진은 intent(JSON) → 결정론적 파이프라인 → seamless SVG candidate 순으로 동작한다.
  LLM은 intent JSON까지만 만들고, 좌표·반복·배치·합성·seamless 보장은 결정론적 엔진이 담당한다.
- 같은 intent·seed·colorway는 바이트 동일 SVG를 생성해야 한다(결정론).
- motif 영속화는 **제품 공유 Supabase 프로젝트**의 `motifs` 테이블(pgvector)에 클라이언트로
  접근한다. **스키마 소유는 이 레포가 아니라 React 모노레포**다 (아래 규칙 참고). 앱은 DDL을
  실행하지 않는다.

## 개발 명령어

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt            # 의존성 설치
.venv/bin/uvicorn app.main:app --reload --port 8000  # 개발 서버
.venv/bin/python -m pytest -q                        # 테스트
```

## DB 접근 (클라이언트 전용)

이 서비스는 **제품 공유 Supabase 프로젝트의 클라이언트**다. 스키마를 소유하지 않고,
직접 Postgres DSN으로 `motifs` 테이블만 읽고/쓴다. supabase CLI도 마이그레이션도 이 레포엔 없다.

```bash
export SUPABASE_DB_URL=postgresql://...   # 서버 사이드 전용, RLS 우회 — 클라이언트 노출 금지
.venv/bin/python -m pytest -q             # SUPABASE_DB_URL 미설정 시 in-memory registry로 동작
```

## 마이그레이션 / 스키마 규칙 (필독)

DB 스키마는 **React 모노레포(YeongSeon)가 단독 소유**한다.
이유: 두 레포가 같은 Supabase 프로젝트(같은 Postgres)에 붙으며, Supabase의 마이그레이션 원장
(`supabase_migrations.schema_migrations`)은 DB당 하나뿐이라 두 곳에서 push하면 갈라진다
(divergence). 한쪽의 `db reset`이 다른 쪽 스키마까지 날린다.

1. **이 레포는 마이그레이션을 만들지 않는다.** `supabase/migrations/`, `supabase db push`,
   `supabase db reset`을 이 레포에서 실행하지 말 것. `make db-new` 류 마이그레이션 생성 명령도 없다.
2. **스키마 변경(`motifs` 포함)은 모노레포에서.** 모노레포 규칙대로 `supabase/schemas/*.sql`을
   고치고 `pnpm db:new`로 마이그레이션을 생성한다 — 직접 생성·임의 타임스탬프 금지, 이미 생성된
   (특히 원격 push된) 마이그레이션 수정·삭제·이름 변경 금지(변경은 새 마이그레이션으로).
   `motifs` 기준 정의는 ARCHITECTURE.md "영속화" 섹션 참고.
3. **이 레포는 DSN 클라이언트로만 접근한다.** `app/motifs/store.py`가 `SUPABASE_DB_URL`로
   `motifs` 테이블을 읽고/쓴다. 앱 런타임은 DDL을 실행하지 않는다.
4. **DSN은 서버 사이드 전용** — direct connection은 RLS를 우회하므로 절대 클라이언트에 노출 금지.
