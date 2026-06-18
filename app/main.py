from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import export, generate, health, palettes
from app.core.config import get_settings
from app.core.observability import (
    configure_logging,
    get_request_id,
    new_request_id,
    set_request_id,
)
from app.render.raster import RasterError

REQUEST_ID_HEADER = "X-Request-ID"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(title=settings.app_name, debug=settings.debug)
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
