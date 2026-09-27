import asyncio
import logging

from pydantic import ValidationError

from app.errors import AnalysisFailure
from app.providers import AnalysisProvider
from app.schemas import AnalysisRequest, AnalysisResponse

logger = logging.getLogger(__name__)


def invalid_response() -> AnalysisFailure:
    return AnalysisFailure(502, "INVALID_AI_RESPONSE", "분석 결과를 검증하지 못했습니다.")


class AnalysisService:
    def __init__(self, provider: AnalysisProvider, timeout_seconds: float):
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    async def analyze(self, request: AnalysisRequest) -> AnalysisResponse:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                payload = await self.provider.analyze(request)
            response = AnalysisResponse.model_validate(payload)
        except AnalysisFailure:
            raise
        except TimeoutError:
            raise AnalysisFailure(
                504, "AI_TIMEOUT", "분석 응답 대기 시간이 초과되었습니다."
            ) from None
        except ValidationError:
            raise invalid_response() from None
        except Exception as exc:
            # Provider messages and tracebacks may contain care records or credentials.
            logger.warning("Analysis provider failed: %s", type(exc).__name__)
            raise AnalysisFailure(
                502, "AI_UNAVAILABLE", "분석 서비스를 사용할 수 없습니다."
            ) from None

        if response.request_id != request.request_id:
            raise invalid_response()
        candidates = {item.item_id: item for item in request.candidates}
        for suggestion in response.suggestions:
            if suggestion.evidence not in request.content:
                raise invalid_response()
            for item_id in suggestion.candidate_item_ids:
                candidate = candidates.get(item_id)
                if candidate is None or candidate.type != suggestion.type:
                    raise invalid_response()
            if suggestion.target_item_id is not None:
                target = candidates.get(suggestion.target_item_id)
                if (
                    target is None
                    or target.type != suggestion.type
                    or target.version != suggestion.expected_version
                ):
                    raise invalid_response()
        return response
