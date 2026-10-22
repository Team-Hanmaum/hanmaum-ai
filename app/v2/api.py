"""Bounded v2 HTTP input, immutable validation and safe error translation."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from starlette.requests import ClientDisconnect

from app.errors import AnalysisFailure
from app.security import require_internal_key
from app.v2.comparison import ComparisonUnavailable
from app.v2.diagnostics import FailureEvent, record_failure
from app.v2.limits import REQUEST_BYTES
from app.v2.openapi import ANALYSIS_OPENAPI, ANALYSIS_RESPONSES, READINESS_RESPONSES
from app.v2.receipts import store_unavailable
from app.v2.resources import timed_out
from app.v2.validation import parse_request, serialize_response

router = APIRouter(dependencies=[Depends(require_internal_key)])


def not_configured():
    return AnalysisFailure(503, "AI_NOT_CONFIGURED", "분석 제공자가 설정되지 않았습니다.")


async def read_body(request: Request, budget: float) -> bytes:
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise AnalysisFailure(422, "INVALID_REQUEST", "비압축 UTF-8 JSON 요청이 필요합니다.")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise AnalysisFailure(422, "INVALID_REQUEST", "JSON 요청이 필요합니다.")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdigit():
            raise AnalysisFailure(422, "INVALID_REQUEST", "요청 길이 형식이 올바르지 않습니다.")
        significant = length.lstrip("0") or "0"
        if len(significant) > 7 or int(significant) > REQUEST_BYTES:
            raise AnalysisFailure(413, "REQUEST_TOO_LARGE", "요청 본문 크기가 제한을 초과했습니다.")
    raw = bytearray()
    async with asyncio.timeout(budget):
        async for chunk in request.stream():
            if len(chunk) > REQUEST_BYTES - len(raw):
                raise AnalysisFailure(
                    413, "REQUEST_TOO_LARGE", "요청 본문 크기가 제한을 초과했습니다."
                )
            raw.extend(chunk)
    return bytes(raw)


async def _wait_disconnect(request):
    while True:
        if (await request.receive())["type"] == "http.disconnect":
            return


async def until_disconnect(request, operation):
    """Start monitoring only after read_body has consumed the final body event."""
    work = asyncio.create_task(operation)
    disconnect = asyncio.create_task(_wait_disconnect(request))
    try:
        done, _ = await asyncio.wait({work, disconnect}, return_when=asyncio.FIRST_COMPLETED)
        if disconnect in done:
            raise ClientDisconnect()
        return work.result()
    finally:
        for task in (work, disconnect):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, disconnect, return_exceptions=True)


@router.post(
    "/v2/analyses",
    tags=["analyses"],
    summary="v2 돌봄 기록 분석",
    description=(
        "Authorize에 BE↔AI 내부 인증키를 입력한 뒤 본문과 X-Request-Id를 함께 지정합니다. "
        "v2 기본 설정은 disabled이며 정상 요청에도 503 AI_NOT_CONFIGURED를 반환합니다. "
        "stub/openai 활성화에는 별도 SQLite 수신 저장소와 HMAC 키가 필요하며, "
        "openai는 서버의 OPENAI_API_KEY를 사용해 실제 분석합니다. "
        "stub은 실제 분석 없이 빈 제안을 반환합니다. "
        "동일 ID·본문 재전송은 409 REQUEST_ALREADY_RECEIVED, "
        "같은 ID·다른 본문은 409 REQUEST_ID_REUSED입니다. "
        "명시적인 새 실행은 헤더·본문의 requestId를 함께 변경하고 recordedAt은 유지합니다. "
        "자동 재시도·v1 fallback은 없습니다."
    ),
    openapi_extra=ANALYSIS_OPENAPI,
    responses=ANALYSIS_RESPONSES,
)
async def analyze(request: Request):
    runtime = request.app.state.v2_runtime
    settings = runtime.settings
    lease = runtime.admissions.acquire()
    # The middleware releases the owner only after the bounded response send.
    request.scope["v2.admission"] = lease
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.total_timeout_seconds
    try:
        async with asyncio.timeout(settings.total_timeout_seconds):
            raw = await read_body(request, settings.body_timeout_seconds)

            async def process():
                parsed = await runtime.cpu.run(
                    parse_request,
                    raw,
                    budget=min(settings.worker_timeout_seconds, deadline - loop.time()),
                    leases=(lease,),
                )
                try:
                    header_id = UUID(request.headers.get("x-request-id", ""))
                except ValueError:
                    raise AnalysisFailure(
                        422, "INVALID_REQUEST", "요청 ID 형식이 올바르지 않습니다."
                    ) from None
                if header_id != parsed.request_id:
                    raise AnalysisFailure(
                        400, "REQUEST_ID_MISMATCH", "헤더와 본문의 요청 ID가 다릅니다."
                    )
                if runtime.service is None:
                    raise not_configured()
                result = await runtime.service.analyze_request(
                    parsed, deadline=deadline, admission=lease
                )
                try:
                    body = await runtime.cpu.run(
                        serialize_response,
                        result,
                        budget=min(settings.worker_timeout_seconds, deadline - loop.time()),
                        leases=(lease,),
                    )
                except AnalysisFailure as error:
                    if error.code == "INVALID_AI_RESPONSE":
                        record_failure(FailureEvent.PROVIDER_INVALID_RESPONSE)
                    raise
                return Response(
                    body,
                    media_type="application/json",
                    headers={
                        "Cache-Control": "no-store",
                        "X-Request-Id": str(parsed.request_id),
                        "X-AI-Provider": settings.provider,
                    },
                )

            return await until_disconnect(request, process())
    except TimeoutError:
        raise timed_out() from None
    except ComparisonUnavailable:
        raise AnalysisFailure(
            503, "REQUEST_COMPARISON_UNAVAILABLE", "수신 기록을 안전하게 비교할 수 없습니다."
        ) from None


@router.get(
    "/health/ready/v2",
    tags=["health"],
    summary="v2 분석 준비 상태",
    description=(
        "내부 인증 후 설정·저장소를 확인합니다. "
        "제공자 호출·기록 등록·실제 모델 품질 검사는 하지 않습니다."
    ),
    responses=READINESS_RESPONSES,
)
async def readiness(request: Request):
    runtime = request.app.state.v2_runtime
    if runtime.service is None or runtime.service.provider is None:
        raise not_configured()
    # A read checks schema and accessibility without creating a record or invoking a provider.
    await runtime.service.storage.run(
        runtime.service.receipts.store.get,
        "v2",
        UUID(int=0),
        budget=runtime.settings.worker_timeout_seconds,
        failure=store_unavailable(),
    )
    return {"status": "UP", "provider": runtime.settings.provider}
