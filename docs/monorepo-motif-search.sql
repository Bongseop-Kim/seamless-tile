-- motifs vector-search 지원 스키마/인덱스 — **YeongSeon(React 모노레포)에서만 적용**
-- =============================================================================
-- 이 레포(seamless-tile)는 Supabase의 DB client일 뿐 스키마를 소유하지 않는다.
-- (AGENTS.md / ARCHITECTURE.md "Persistence와 소유권") 따라서 이 파일은 **참고용 스니펫**이며
-- 이 레포에서 실행하지 않는다. 모노레포 규칙대로 `supabase/schemas/*.sql`을 고치고 `pnpm db:new`로
-- 마이그레이션을 생성할 것 — 직접 생성·임의 타임스탬프·기존(원격 push된) 마이그레이션 수정 금지.
--
-- 배경: 런타임 코드(app/motifs/store.py:find_best_by_embedding)가 cosine 랭킹을 Postgres로 내렸다.
--   SELECT id, variant_group, 1 - (embedding <=> $q::extensions.vector) AS similarity
--   FROM motifs
--   WHERE scope = $scope AND embedding IS NOT NULL
--     AND extensions.vector_dims(embedding) = $dim          -- 차원 가드
--   ORDER BY embedding <=> $q::extensions.vector ASC, id ASC -- 동점 시 최저 id
--   LIMIT 1;
-- 이 쿼리는 아래 인덱스가 하나도 없어도 정확(seq scan)하게·결정론적으로 동작한다.
-- 인덱스는 순수 latency 최적화이며, 적용 순서는 1 → 2 → (필요해지면) 3 이다.


-- 1) (#2) 임베딩 차원 고정 — false-mix 방지
-- -----------------------------------------------------------------------------
-- 현재 컬럼은 차원 없는 `extensions.vector`로 추정된다(4-d 테스트 row가 들어감). 모델
-- text-embedding-3-small의 기본 차원은 1536이다. 혼합 차원 row가 생기면 `<=>`가 에러를 내므로
-- (런타임은 vector_dims 가드로 방어 중) 가능하면 컬럼 차원을 고정한다.
--
--   ALTER TABLE public.motifs
--     ALTER COLUMN embedding TYPE extensions.vector(1536);
--
-- 차원 고정이 어렵다면(다중 모델 등) 메타 + CHECK로 혼합을 막는다:
--   ALTER TABLE public.motifs ADD COLUMN IF NOT EXISTS embedding_model text;
--   ALTER TABLE public.motifs ADD COLUMN IF NOT EXISTS embedding_dim   int;
--   ALTER TABLE public.motifs ADD CONSTRAINT motifs_embedding_dim_ck
--     CHECK (embedding IS NULL OR extensions.vector_dims(embedding) = embedding_dim);
-- 컬럼을 vector(1536)으로 고정하면 런타임의 vector_dims 가드는 항상 참인 no-op가 된다.


-- 2) (#5) 필터용 B-tree 인덱스 — vector 인덱스보다 먼저
-- -----------------------------------------------------------------------------
-- scope hard filter와 variant_group 풀 조회를 받쳐준다. (id를 두 번째 키로 두어 ORDER BY id 정렬도 도움)
CREATE INDEX IF NOT EXISTS motifs_scope_id_idx
  ON public.motifs (scope, id);

CREATE INDEX IF NOT EXISTS motifs_variant_group_id_idx
  ON public.motifs (variant_group, id)
  WHERE variant_group IS NOT NULL;


-- 3) (#6) HNSW — row가 수만 건 이상으로 커진 "뒤에만"
-- -----------------------------------------------------------------------------
-- 주의(#8): HNSW는 approximate search다. 결정론이 중요한 서비스이므로 처음에는 exact 쿼리로
-- 운영하고, row 수가 커져 latency가 문제될 때 도입한다. 도입 시 hnsw.ef_search와 filtered query의
-- recall을 별도로 측정할 것. 우리는 scope hard filter를 항상 쓰므로 scope별 partial 인덱스도 검토.
--
--   CREATE INDEX motifs_embedding_hnsw_cosine_idx
--     ON public.motifs USING hnsw (embedding extensions.vector_cosine_ops)
--     WHERE embedding IS NOT NULL;
--
--   -- scope별 partial (row가 충분히 많을 때)
--   CREATE INDEX motifs_embedding_whole_hnsw_idx
--     ON public.motifs USING hnsw (embedding extensions.vector_cosine_ops)
--     WHERE scope = 'whole'   AND embedding IS NOT NULL;
--   CREATE INDEX motifs_embedding_partial_hnsw_idx
--     ON public.motifs USING hnsw (embedding extensions.vector_cosine_ops)
--     WHERE scope = 'partial' AND embedding IS NOT NULL;
