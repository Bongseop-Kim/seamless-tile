from fastapi import FastAPI

from app.api.routes import generate, health, palettes
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug)
    app.include_router(health.router, prefix=settings.api_v1_prefix)
    app.include_router(palettes.router, prefix=settings.api_v1_prefix)
    app.include_router(generate.router, prefix=settings.api_v1_prefix)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"name": settings.app_name, "docs": "/docs"}

    return app


app = create_app()
