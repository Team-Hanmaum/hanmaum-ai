from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import Settings
from app.errors import install_error_handlers
from app.providers import AnalysisProvider, create_provider
from app.services import AnalysisService
from app.v2.api import router as v2_router
from app.v2.config import V2Settings
from app.v2.openapi import install_openapi
from app.v2.runtime import V2Runtime
from app.v2.services import AnalysisService as V2AnalysisService
from app.v2.transport import V2Transport


def create_app(
    settings: Settings | None = None,
    provider: AnalysisProvider | None = None,
    *,
    v2_settings: V2Settings | None = None,
    v2_service: V2AnalysisService | None = None,
) -> FastAPI:
    settings = settings if settings is not None else Settings()
    public_docs = settings.app_env != "prod"
    v2_runtime = V2Runtime(v2_settings or V2Settings(), settings.app_env, v2_service)
    if v2_runtime.settings.provider == "stub" and "app_env" not in settings.model_fields_set:
        raise ValueError("v2 stub requires explicit APP_ENV=local or test")
    internal_key = settings.ai_internal_api_key.get_secret_value().encode("utf-8")
    if (
        v2_runtime.settings.provider == "openai"
        and v2_runtime.settings.openai_api_key.get_secret_value().encode() == internal_key
    ):
        raise ValueError("OpenAI and internal API keys must be separate")
    if v2_runtime.service is not None and any(
        key.get_secret_value() == internal_key for key in v2_runtime.settings.hmac_keys.values()
    ):
        raise ValueError("v2 HMAC keys must be separate from the internal API key")

    @asynccontextmanager
    async def lifespan(app):
        yield
        await v2_runtime.close()

    app = FastAPI(
        title="한마음 AI API",
        version="0.1.0",
        description="Spring 내부 호출용 돌봄 기록 분석 API",
        docs_url="/docs" if public_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if public_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.analysis_service = AnalysisService(
        provider if provider is not None else create_provider(settings),
        settings.analysis_timeout_seconds,
    )
    install_error_handlers(app)
    app.include_router(router)
    app.state.v2_runtime = v2_runtime
    app.add_middleware(V2Transport)
    app.include_router(v2_router)
    install_openapi(app)
    return app
