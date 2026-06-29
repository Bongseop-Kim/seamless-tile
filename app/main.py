import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.adapters.embedding import client_from_settings as embedding_client_from_settings
from app.adapters.embedding import set_default_embedding_client
from app.adapters.gemini import client_from_settings
from app.adapters.llm import set_default_client
from app.adapters.recraft import client_from_settings as recraft_client_from_settings
from app.adapters.recraft import set_default_recraft_client
from app.api.routes import export, generate, health, palettes
from app.core.config import get_settings
from app.core.observability import (
    configure_logging,
    get_request_id,
    new_request_id,
    set_request_id,
)
from app.motifs.registry import hydrate_from_store
from app.motifs.store import set_default_store, store_from_settings
from app.render.raster import RasterError

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Install the motif store and hydrate the in-memory registry at boot.

    Unconfigured (no SUPABASE_DB_URL) is a no-op; any store error is logged and
    swallowed so boot never crashes — correctness still holds because get_motif
    lazy-loads on a cold miss. The synchronous hydration runs once at startup only
    (never in the request path).
    """
    settings = get_settings()
    store = store_from_settings(settings)
    set_default_store(store)
    if store is not None:
        try:
            count = hydrate_from_store(store)
            logger.info("motif store hydrated: %d motif(s)", count)
        except Exception:
            logger.warning("motif store hydration skipped (store error)", exc_info=True)
    else:
        logger.info("motif store unconfigured; in-memory registry only")

    # Install required model clients. This service's product path depends on both
    # prompt interpretation and descriptor embeddings, so missing keys are a startup
    # configuration error rather than a degraded runtime mode.
    missing = [
        name
        for name, value in (
            ("GEMINI_API_KEY", settings.gemini_api_key),
            ("OPENAI_API_KEY", settings.openai_api_key),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise RuntimeError(f"missing required model configuration: {', '.join(missing)}")

    llm_client = client_from_settings(settings)
    if llm_client is None:
        raise RuntimeError("GEMINI_API_KEY is required to configure the LLM client")
    set_default_client(llm_client)
    logger.info("LLM client configured (Gemini)")

    embedding_client = embedding_client_from_settings(settings)
    if embedding_client is None:
        raise RuntimeError("OPENAI_API_KEY is required to configure the embedding client")
    set_default_embedding_client(embedding_client)
    logger.info("embedding client configured (OpenAI)")

    # Install the Recraft vector client when a key is configured; unset => detailed/
    # multicolor misses (D11 routing) surface a 502 (no generator).
    recraft_client = recraft_client_from_settings(settings)
    set_default_recraft_client(recraft_client)
    logger.info(
        "Recraft client %s",
        "configured" if recraft_client is not None else "unconfigured (detailed miss -> 502)",
    )
    yield
    # One connection per operation — nothing to tear down.


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    app.include_router(health.router, prefix=settings.api_v1_prefix)
    app.include_router(palettes.router, prefix=settings.api_v1_prefix)
    app.include_router(generate.router, prefix=settings.api_v1_prefix)
    app.include_router(export.router, prefix=settings.api_v1_prefix)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = set_request_id(request.headers.get(REQUEST_ID_HEADER) or new_request_id())
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def on_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Request-schema failure is a 4xx (distinct from 422 semantic intent validation).
        return _error_response(400, jsonable_encoder(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def on_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Route-raised HTTPExceptions (422 semantic, 500, 502, 404 ...) still carry the
        # request_id in the body, not just the X-Request-ID header.
        return _error_response(exc.status_code, exc.detail)

    @app.exception_handler(RasterError)
    async def on_raster_error(request: Request, exc: RasterError) -> JSONResponse:
        return _error_response(502, [str(exc)])

    @app.get("/")
    def root() -> dict[str, str]:
        return {"name": settings.app_name, "docs": "/docs"}

    return app


def _error_response(status_code: int, detail) -> JSONResponse:
    request_id = get_request_id()
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "request_id": request_id},
        headers={REQUEST_ID_HEADER: request_id},
    )


app = create_app()
