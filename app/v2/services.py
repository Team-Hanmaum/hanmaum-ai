"""Internal v2 orchestration, deliberately not mounted on an HTTP route yet."""

import asyncio
import math
from typing import Protocol

from app.errors import AnalysisFailure
from app.v2.receipts import ReceiptGate
from app.v2.schemas import AnalysisRequest, AnalysisResponse
from app.v2.validation import parse_request, validate_response


class AnalysisProvider(Protocol):
    async def analyze(self, request: AnalysisRequest) -> object: ...


class AnalysisService:
    def __init__(
        self,
        *,
        receipts: ReceiptGate,
        provider: AnalysisProvider | None,
        timeout_seconds: float = 20,
    ):
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 25:
            raise ValueError("Analysis timeout must be positive and at most 25 seconds")
        self.receipts = receipts
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    async def analyze(self, raw: bytes | str) -> AnalysisResponse:
        """Caller must authenticate, check header ID and bound input before this service."""
        request = parse_request(raw)
        if self.provider is None:
            raise AnalysisFailure(503, "AI_NOT_CONFIGURED", "분석 제공자가 설정되지 않았습니다.")
        # Each operation opens its own connection in the worker. Cancellation may leave a
        # committed receipt, but never starts a provider in that worker or removes a receipt.
        await asyncio.to_thread(self.receipts.claim, request)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                payload = await self.provider.analyze(request)
        except TimeoutError:
            raise AnalysisFailure(
                504, "AI_TIMEOUT", "분석 응답 대기 시간이 초과되었습니다."
            ) from None
        except Exception:
            # Do not relay even a provider-supplied AnalysisFailure with arbitrary content.
            raise AnalysisFailure(
                502, "AI_UNAVAILABLE", "분석 서비스를 사용할 수 없습니다."
            ) from None
        return validate_response(request, payload)
