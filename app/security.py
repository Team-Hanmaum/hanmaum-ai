import secrets
from typing import Annotated

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from app.errors import AnalysisFailure

internal_api_key = APIKeyHeader(name="X-Internal-Api-Key", auto_error=False)


async def require_internal_key(
    request: Request,
    provided: Annotated[str | None, Security(internal_api_key)],
) -> None:
    expected = request.app.state.settings.ai_internal_api_key.get_secret_value()
    if provided is None or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise AnalysisFailure(401, "UNAUTHORIZED", "내부 인증이 필요합니다.")
