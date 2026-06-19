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
    # fakes). Embeddings (OpenAI) are a separate S11 concern, not configured here.
    gemini_api_key: str | None = None  # env: GEMINI_API_KEY
    gemini_model: str = "gemini-2.5-flash-lite"  # chat model id passed to GeminiClient (P0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Code versions recorded in candidate reproduction metadata (not runtime settings).
ENGINE_VERSION = "0.1.0"
REGISTRY_VERSION = "0.1.0"

# Allowed raster DPIs at the production/raster boundary.
DEFAULT_DPI = 300
ALLOWED_DPI = (150, 300, 600)
