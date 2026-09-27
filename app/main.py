from fastapi import FastAPI

from app.api import router
from app.config import Settings
from app.errors import install_error_handlers
from app.providers import AnalysisProvider, create_provider
from app.services import AnalysisService


def create_app(
    settings: Settings | None = None, provider: AnalysisProvider | None = None
) -> FastAPI:
    settings = settings if settings is not None else Settings()
    public_docs = settings.app_env != "prod"
    app = FastAPI(
        title="한마음 AI API",
        version="0.1.0",
        description="Spring 내부 호출용 돌봄 기록 분석 API",
        docs_url="/docs" if public_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if public_docs else None,
    )
    app.state.settings = settings
    app.state.analysis_service = AnalysisService(
        provider if provider is not None else create_provider(settings),
        settings.analysis_timeout_seconds,
    )
    install_error_handlers(app)
    app.include_router(router)
    return app
