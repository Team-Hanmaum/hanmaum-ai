"""v2 orchestration with receipt-first admission and task-lifetime capacity."""

import asyncio
import math
from typing import Protocol

from app.errors import AnalysisFailure
from app.v2.diagnostics import FailureEvent, record_failure
from app.v2.receipts import ReceiptGate, store_unavailable
from app.v2.resources import Capacity, Workers, timed_out
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
        concurrency: int = 2,
        worker_timeout_seconds: float = 1,
    ):
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 25:
            raise ValueError("Analysis timeout must be positive and at most 25 seconds")
        self.receipts = receipts
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        if not math.isfinite(worker_timeout_seconds) or worker_timeout_seconds <= 0:
            raise ValueError("Worker timeout must be finite and positive")
        self.worker_timeout_seconds = worker_timeout_seconds
        self.slots = Capacity(concurrency)
        self.cpu = Workers(4)
        self.storage = Workers(4)
        self.tasks: set[asyncio.Task] = set()

    async def analyze(self, raw: bytes | str) -> AnalysisResponse:
        """Internal entry; HTTP authenticates and parses via bounded workers separately."""
        request = parse_request(raw)
        return await self.analyze_request(request)

    async def analyze_request(self, request: AnalysisRequest, *, deadline=None, admission=None):
        loop = asyncio.get_running_loop()

        def budget():
            return min(
                self.worker_timeout_seconds,
                deadline - loop.time() if deadline is not None else self.worker_timeout_seconds,
            )

        provider = self.provider
        if provider is None:
            record_failure(FailureEvent.PROVIDER_CONFIGURATION)
            raise AnalysisFailure(503, "AI_NOT_CONFIGURED", "분석 제공자가 설정되지 않았습니다.")
        await self.storage.run(
            self.receipts.check_existing,
            request,
            budget=budget(),
            leases=(admission,),
            failure=store_unavailable(),
        )
        lease = self.slots.acquire()
        try:
            # Recheck/register atomically after admission. A late commit never starts a provider.
            await self.storage.run(
                self.receipts.claim,
                request,
                budget=budget(),
                leases=(admission, lease),
                failure=store_unavailable(),
            )
            if deadline is not None and loop.time() >= deadline:
                raise timed_out()
            payload = await self._invoke(request, provider, lease, admission, deadline)
            try:
                return await self.cpu.run(
                    validate_response,
                    request,
                    payload,
                    budget=budget(),
                    leases=(admission, lease),
                )
            except AnalysisFailure as error:
                if error.code == "INVALID_AI_RESPONSE":
                    record_failure(FailureEvent.PROVIDER_INVALID_RESPONSE)
                raise
        finally:
            lease.release()

    async def _invoke(self, request, provider, lease, admission, overall_deadline):

        async def invoke_provider():
            return await provider.analyze(request)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        if overall_deadline is not None:
            # Validation and serialization each have one worker budget.
            deadline = min(deadline, overall_deadline - 2 * self.worker_timeout_seconds)
        if deadline <= loop.time():
            raise timed_out()
        task = asyncio.create_task(invoke_provider())
        _provider_tasks.add(task)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(_provider_finished)
        lease.hold(task)
        if admission is not None:
            admission.hold(task)
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
            if task.cancelled():
                raise RuntimeError("Provider task cancelled")
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
        return payload
