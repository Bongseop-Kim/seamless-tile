from functools import lru_cache

from pydantic import field_validator
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
    max_placement_instances: int = 50_000
    max_svg_bytes: int = 2_000_000

    # Supabase persistence (session 9). The motif store is "configured" iff
    # supabase_db_url is set; unset => in-memory registry only (tests, local dev).
    supabase_db_url: str | None = None  # postgresql://...  (env: SUPABASE_DB_URL)
    supabase_service_key: str | None = None

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
    motif_similarity_tau: float = 0.60  # env: MOTIF_SIMILARITY_TAU

    # Recraft motif generation (session 13, D11/M1). The detailed/painterly miss path
    # routes to Recraft; its output is path-flattened and its color count capped to this
    # many slots (excess colors are deterministically merged; spec §6.2/§12).
    recraft_max_color_slots: int = 6  # env: RECRAFT_MAX_COLOR_SLOTS
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
    motif_max_aspect_ratio: float = 20.0  # env: MOTIF_MAX_ASPECT_RATIO
    motif_edge_seam_tol: float = 2.0  # env: MOTIF_EDGE_SEAM_TOL
    motif_render_check: bool = True  # env: MOTIF_RENDER_CHECK

    @field_validator("motif_similarity_tau")
    @classmethod
    def _validate_motif_similarity_tau(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("motif_similarity_tau must be between 0 and 1")
        return value

    @field_validator("recraft_max_color_slots")
    @classmethod
    def _validate_recraft_max_color_slots(cls, value: int) -> int:
        if value < 1:
            raise ValueError("recraft_max_color_slots must be at least 1")
        return value

    @field_validator("motif_max_aspect_ratio")
    @classmethod
    def _validate_motif_max_aspect_ratio(cls, value: float) -> float:
        if value <= 1.0:
            raise ValueError("motif_max_aspect_ratio must be greater than 1")
        return value

    @field_validator("motif_edge_seam_tol")
    @classmethod
    def _validate_motif_edge_seam_tol(cls, value: float) -> float:
        if value <= 0.0:
            raise ValueError("motif_edge_seam_tol must be greater than 0")
        return value

    @field_validator("max_placement_instances", "max_svg_bytes")
    @classmethod
    def _validate_positive_ceiling(cls, value: int) -> int:
        if value < 1:
            raise ValueError("resource ceiling must be at least 1")
        return value


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
