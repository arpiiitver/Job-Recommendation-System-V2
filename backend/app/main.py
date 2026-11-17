from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.core.config import DEV_JWT_SECRET, get_settings
from app.services.llm import LlmStrictError


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.is_production:
        # Fail fast on unsafe production config instead of serving with it.
        if settings.jwt_secret_key == DEV_JWT_SECRET or len(settings.jwt_secret_key) < 32:
            raise RuntimeError("JWT_SECRET_KEY must be set to a unique value of 32+ chars in production.")
        if any("localhost" in o for o in settings.cors_origin_list):
            import logging

            logging.getLogger(__name__).warning("CORS_ORIGINS still contains localhost in production.")
    application = FastAPI(
        title="JobRec API",
        description="Job recommendation platform API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix="/api/v1")

    @application.exception_handler(LlmStrictError)
    async def _llm_strict_error(request: Request, exc: LlmStrictError) -> JSONResponse:
        # LLM_STRICT=true trial mode: surface AI failures as a clear 502
        # instead of the routes' silent deterministic fallbacks.
        return JSONResponse(status_code=502, content={"detail": f"AI failure: {exc}"})

    return application


app = create_app()
