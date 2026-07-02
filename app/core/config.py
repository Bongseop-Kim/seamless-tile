from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "seamless tile API"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Raster (Phase 2): resvg/rsvg-convert binary; None => auto-detect on PATH.
    renderer_bin: str | None = None
    max_dpi: int = 1200
    max_tile_mm: float = 2000.0

    # Resource ceilings bounding a single intent's work/output (DoS guard, audit A1/A3).
    # max_placement_instances caps a layer's enumerated placement points; max_svg_bytes
    # caps the composed document before the sanitize re-parse (same order as the export
    # input cap ExportRequest.svg). Tunable per deployment.
    max_placement_instances: int = Field(50_000, ge=1)
    max_svg_bytes: int = Field(2_000_000, ge=1)

    # Supabase persistence (session 9). The motif store is "configured" iff
    # supabase_db_url is set; unset => in-memory registry only (tests, local dev).
    supabase_db_url: str | None = None  # postgresql://...  (env: SUPABASE_DB_URL)
    supabase_service_key: str | None = None

    # Candidate preview PNGs (generate route). SVG is rendered once at generate time and
    # uploaded to Supabase Storage; the response carries only the public URL (no re-render
    # on access). Configured iff supabase_url + supabase_service_key are set; unset =>
    # preview upload is a graceful no-op (png_url is null + a warning), like the motif store.
    supabase_url: str | None = None  # https://<ref>.supabase.co  (env: SUPABASE_URL)
    preview_bucket: str = "seamless-previews"  # env: PREVIEW_BUCKET
    preview_dpi: int = Field(192, ge=1, le=1200)  # env: PREVIEW_DPI; tile preview raster resolution (2x)

    # Fabric texture render (session 15, /finalize). Resolution of the textured PNG;
    # higher than preview for a convincing cloth look. Capped by max_dpi. Uploads reuse
    # the preview bucket under a ``fabric/`` prefix.
    fabric_dpi: int = Field(300, ge=1, le=1200)  # env: FABRIC_DPI

    # Generate 응답 캐시(in-process LRU). 동일 요청은 직전 candidates+preview URL을 그대로
    # 반환해 adapter/엔진/렌더+업로드 작업을 건너뜀. 채팅 표면에서는 "같은 입력→바이트 동일"이
    # 기본이면 안 되므로 기본 0(비활성: lookup+store 생략). nonzero는 결정론/repro 디버깅용 opt-in.
    # ponytail: 프로세스-로컬(워커별 독립); 멀티워커 hit-rate가 문제되면 공유 캐시로 승급.
    generate_cache_size: int = Field(0, ge=0)  # env: GENERATE_CACHE_SIZE (0=off 기본; nonzero=repro/debug)
    # Max fraction of a stripe period its bands may cover when an opaque background sits
    # beneath; the remainder is guaranteed to stay visible so the named ground color (and
    # any under-stripe texture) shows through. env: STRIPE_MAX_BAND_COVERAGE
    stripe_max_band_coverage: float = Field(0.75, ge=0.1, le=1.0)
    # Generated diagonal stripes are normalized to 45 deg with this many repeats per tile
    # (count = 2*k at 45 deg, so k = repeats//2; 2 => one big pair of diagonal stripes).
    # env: STRIPE_DIAGONAL_REPEATS
    stripe_diagonal_repeats: int = Field(2, ge=2)

    # Chat LLM (session 10, D12). app.main requires gemini_api_key and installs a
    # GeminiClient as the default LLM client at boot. Unit tests inject fakes directly.
    gemini_api_key: str | None = None  # env: GEMINI_API_KEY
    gemini_model: str = "gemini-2.5-flash-lite"  # chat model id passed to GeminiClient (P0)
    # Sampling temperature for intent generation. >0 lets the model emit genuinely
    # distinct designs; determinism is unaffected (the adapter freezes the finalized
    # intent in its cache, so the contract does not depend on temperature).
    gemini_temperature: float = Field(0.7, ge=0.0, le=2.0)  # env: GEMINI_TEMPERATURE

    # Embedding model (session 11, D12). app.main requires openai_api_key and installs an
    # OpenAIEmbeddingClient as the default. Unit tests can still call the resolver with no
    # client to exercise exact/hard-filter behavior.
    # motif_similarity_tau is the cosine threshold for "reuse vs generate"; it is a
    # reuse-first start value (spec §6.1/D13) pending empirical calibration (spec §12).
    openai_api_key: str | None = None  # env: OPENAI_API_KEY
    embedding_model: str = "text-embedding-3-small"  # env: EMBEDDING_MODEL
    motif_similarity_tau: float = Field(0.60, ge=0.0, le=1.0)  # env: MOTIF_SIMILARITY_TAU

    # Conversational sessions (session 16). Interactive motif reuse presents this many
    # free candidates before offering "generate new" (Recraft) behind a confirm gate.
    motif_candidate_top_k: int = Field(5, ge=1)  # env: MOTIF_CANDIDATE_TOP_K

    # Recraft motif generation (session 13, D11/M1). The detailed/painterly miss path
    # routes to Recraft; its output is path-flattened and its color count capped to this
    # many slots (excess colors are deterministically merged; spec §6.2/§12).
    recraft_max_color_slots: int = Field(6, ge=1)  # env: RECRAFT_MAX_COLOR_SLOTS
    # Recraft vector API. When recraft_api_key is set, app.main installs a
    # RecraftHTTPClient as the default Recraft client at boot; unset => detailed misses
    # surface 502 (no generator). The vector endpoint returns an SVG file per slot.
    recraft_api_key: str | None = None  # env: RECRAFT_API_KEY
    recraft_model: str = "recraftv4_1_vector"  # env: RECRAFT_MODEL (a *_vector model)
    recraft_style: str = ""  # env: RECRAFT_STYLE (optional; vector model drives SVG output)
    recraft_size: str = "1024x1024"  # env: RECRAFT_SIZE
    recraft_response_format: str = "url"  # env: RECRAFT_RESPONSE_FORMAT (url | b64_json)
    recraft_base_url: str = "https://external.api.recraft.ai/v1"  # env: RECRAFT_BASE_URL

    # Motif Tier1 structural heuristics (spec §8/§12). The intake gate
    # (normalize_motif_svg) rejects structurally bad motifs in the request path.
    # - max_aspect_ratio: reject a too-thin/elongated bbox (longest/shortest side).
    # - edge_seam_tol: per-channel mean edge_seam tolerance for the render-based
    #   overflow guard (aligned with 00-overview edge_seam <= 2.0).
    # - render_check: master switch for the render-dependent checks (#4 render error
    #   + #5 edge_seam); when off, or when no SVG renderer is installed, those checks
    #   are skipped (best-effort; the pure-geometry checks still run).
    motif_max_aspect_ratio: float = Field(20.0, gt=1.0, allow_inf_nan=False)  # env: MOTIF_MAX_ASPECT_RATIO
    motif_edge_seam_tol: float = Field(2.0, gt=0.0, allow_inf_nan=False)  # env: MOTIF_EDGE_SEAM_TOL
    motif_render_check: bool = True  # env: MOTIF_RENDER_CHECK


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Code versions recorded in candidate reproduction metadata (not runtime settings).
# A one-off (unsaved) request's variant selection depends on `% len(pool)`, so reusable
# pool growth can change results; the seal "(prompt, seed, registry_version) -> same
# result" (spec §7.3/D17) only holds if the version tracks the pool. REGISTRY_VERSION is
# NOT bumped by hand for pool changes -- `adapters.registry_fingerprint.registry_version_for`
# derives the stamped value at request time as `REGISTRY_VERSION + "+pool.<hex8>"` over
# the reusable motif ids. An empty/absent pool stamps the bare baseline below.
# Bump REGISTRY_VERSION by hand ONLY for repro-format / registry-schema changes.
ENGINE_VERSION = "0.1.0"
REGISTRY_VERSION = "0.1.0"

# Allowed raster DPIs at the production/raster boundary.
ALLOWED_DPI = (150, 300, 600)
