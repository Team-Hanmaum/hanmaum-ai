"""Internal v2 orchestration, deliberately not mounted on an HTTP route yet."""

import asyncio
import math
from typing import Protocol

from app.errors import AnalysisFailure
from app.v2.diagnostics import FailureEvent, record_failure
from app.v2.receipts import ReceiptGate
from app.v2.schemas import AnalysisRequest, AnalysisResponse
from app.v2.validation import parse_request, validate_response

# Keep strong references even after a caller times out or disconnects. Completion
# consumes exceptions so asyncio never logs provider messages in an unhandled-task error.
_provider_tasks: set[asyncio.Task] = set()
_abandoned_tasks: set[asyncio.Task] = set()


def _provider_finished(task: asyncio.Task) -> None:
    _provider_tasks.discard(task)
    abandoned = task in _abandoned_tasks
    _abandoned_tasks.discard(task)
    if not task.cancelled():
        error = task.exception()
        if abandoned and error is not None:
            record_failure(FailureEvent.PROVIDER_LATE_FAILURE, error)


def _abandon_provider(task: asyncio.Task) -> None:
    if not task.done():
        _abandoned_tasks.add(task)
        task.cancel()


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
        provider = self.provider
        if provider is None:
            record_failure(FailureEvent.PROVIDER_CONFIGURATION)
            raise AnalysisFailure(503, "AI_NOT_CONFIGURED", "분석 제공자가 설정되지 않았습니다.")
        # Each operation opens its own connection in the worker. Cancellation may leave a
        # committed receipt, but never starts a provider in that worker or removes a receipt.
        await asyncio.to_thread(self.receipts.claim, request)

        async def invoke_provider():
            return await provider.analyze(request)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        task = asyncio.create_task(invoke_provider())
        _provider_tasks.add(task)
        task.add_done_callback(_provider_finished)
        try:
            # wait() observes a deadline without waiting for cancellation acknowledgement.
            done, _ = await asyncio.wait({task}, timeout=max(0, deadline - loop.time()))
        except asyncio.CancelledError:
            _abandon_provider(task)
            raise
        if not done or loop.time() >= deadline:
            _abandon_provider(task)
            record_failure(FailureEvent.PROVIDER_TIMEOUT)
            raise AnalysisFailure(504, "AI_TIMEOUT", "분석 응답 대기 시간이 초과되었습니다.")
        try:
            payload = task.result()
        except TimeoutError as error:
            record_failure(FailureEvent.PROVIDER_TIMEOUT, error)
            raise AnalysisFailure(
                504, "AI_TIMEOUT", "분석 응답 대기 시간이 초과되었습니다."
            ) from None
        except Exception as error:
            # Do not relay even a provider-supplied AnalysisFailure with arbitrary content.
            record_failure(FailureEvent.PROVIDER_FAILED, error)
            raise AnalysisFailure(
                502, "AI_UNAVAILABLE", "분석 서비스를 사용할 수 없습니다."
            ) from None
        try:
            return validate_response(request, payload)
        except AnalysisFailure:
            record_failure(FailureEvent.PROVIDER_INVALID_RESPONSE)
            raise
