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
    preview_dpi: int = Field(96, ge=1)  # env: PREVIEW_DPI; tile preview raster resolution

    # Chat LLM (session 10, D12). When gemini_api_key is set, app.main installs a
    # GeminiClient as the default LLM client at boot; unset => no default (tests inject
    # fakes).
    gemini_api_key: str | None = None  # env: GEMINI_API_KEY
    gemini_model: str = "gemini-2.5-flash-lite"  # chat model id passed to GeminiClient (P0)

    # Embedding model (session 11, D12). When openai_api_key is set, app.main installs an
    # OpenAIEmbeddingClient as the default; unset => motif resolver skips the soft-
    # similarity stage and falls back to the S10 exact/hard-filter behavior (graceful).
    # motif_similarity_tau is the cosine threshold for "reuse vs generate"; it is a
    # reuse-first start value (spec §6.1/D13) pending empirical calibration (spec §12).
    openai_api_key: str | None = None  # env: OPENAI_API_KEY
    embedding_model: str = "text-embedding-3-small"  # env: EMBEDDING_MODEL
    motif_similarity_tau: float = Field(0.60, ge=0.0, le=1.0)  # env: MOTIF_SIMILARITY_TAU

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
# A one-off (unsaved) request's variant selection depends on `% len(pool)`, so curated
# pool growth can change results; the seal "(prompt, seed, registry_version) -> same
# result" (spec §7.3/D17) only holds if the version tracks the pool. The pool is mutable
# DB state (promotion via the curation CLI), so REGISTRY_VERSION is NOT bumped by hand for
# pool changes -- `adapters.registry_fingerprint.registry_version_for` derives the stamped
# value at request time as `REGISTRY_VERSION + "+pool.<hex8>"` over the curated ids. An
# empty/absent pool stamps the bare baseline below (degenerate S11 == today's value).
# Bump REGISTRY_VERSION by hand ONLY for repro-format / registry-schema changes.
ENGINE_VERSION = "0.1.0"
REGISTRY_VERSION = "0.1.0"

# Allowed raster DPIs at the production/raster boundary.
DEFAULT_DPI = 300
ALLOWED_DPI = (150, 300, 600)
