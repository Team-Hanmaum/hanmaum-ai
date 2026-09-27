from typing import Any, Protocol

from app.config import Settings
from app.errors import AnalysisFailure
from app.schemas import AnalysisRequest


class AnalysisProvider(Protocol):
    async def analyze(self, request: AnalysisRequest) -> dict[str, Any]: ...


class StubProvider:
    """Local/test-only transport check; performs no semantic analysis."""

    async def analyze(self, request: AnalysisRequest) -> dict[str, Any]:
        return {"requestId": str(request.request_id), "suggestions": []}


class DisabledProvider:
    async def analyze(self, request: AnalysisRequest) -> dict[str, Any]:
        raise AnalysisFailure(503, "AI_NOT_CONFIGURED", "분석 제공자가 설정되지 않았습니다.")


def create_provider(settings: Settings) -> AnalysisProvider:
    if settings.ai_provider == "stub":
        return StubProvider()
    return DisabledProvider()
