from fastapi import FastAPI

from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
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

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "UP"}

    return app
