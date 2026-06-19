from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Code versions recorded in candidate reproduction metadata (not runtime settings).
# Bump REGISTRY_VERSION whenever the curated sampling pool changes (spec §7.3/D17): a
# one-off (unsaved) request's variant selection depends on `% len(pool)`, so pool growth
# can change results; the bump seals "(prompt, seed, registry_version) -> same result".
# In S11 the curated pool is degenerate (<=1, Tier2 promotion is S14), so no bump fires
# yet -- this is the documented contract, exercised in earnest from S14.
ENGINE_VERSION = "0.1.0"
REGISTRY_VERSION = "0.1.0"

# Allowed raster DPIs at the production/raster boundary.
DEFAULT_DPI = 300
ALLOWED_DPI = (150, 300, 600)
