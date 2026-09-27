from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app.errors import AnalysisFailure
from app.schemas import AnalysisRequest, AnalysisResponse
from app.security import require_internal_key

router = APIRouter()


@router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "UP"}


@router.get("/health/ready", tags=["health"])
async def readiness(request: Request) -> dict[str, str]:
    if request.app.state.settings.ai_provider == "disabled":
        raise AnalysisFailure(503, "AI_NOT_CONFIGURED", "분석 제공자가 설정되지 않았습니다.")
    return {"status": "UP"}


@router.post(
    "/v1/analyses",
    response_model=AnalysisResponse,
    tags=["analyses"],
    dependencies=[Depends(require_internal_key)],
    responses={
        400: {"description": "요청 ID 불일치"},
        401: {"description": "내부 인증 실패"},
        422: {"description": "요청 형식 오류"},
        502: {"description": "제공자 오류 또는 잘못된 분석 결과"},
        503: {"description": "제공자 미설정"},
        504: {"description": "분석 타임아웃"},
    },
)
async def analyze(
    body: AnalysisRequest,
    request: Request,
    response: Response,
    x_request_id: Annotated[UUID, Header()],
) -> AnalysisResponse:
    if x_request_id != body.request_id:
        raise AnalysisFailure(400, "REQUEST_ID_MISMATCH", "헤더와 본문의 요청 ID가 다릅니다.")
    result = await request.app.state.analysis_service.analyze(body)
    response.headers["X-Request-Id"] = str(body.request_id)
    response.headers["X-AI-Provider"] = request.app.state.settings.ai_provider
    response.headers["Cache-Control"] = "no-store"
    return result
