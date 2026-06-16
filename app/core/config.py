from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Duego API"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
