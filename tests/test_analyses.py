import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

TEST_KEY = "test-only-internal-key"
pytestmark = pytest.mark.anyio


class StaticProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def analyze(self, request):
        self.calls += 1
        return self.payload


async def send(body, *, provider=None, headers=None, settings=None):
    settings = settings or Settings(
        _env_file=None, app_env="test", ai_provider="stub", ai_internal_api_key=TEST_KEY
    )
    app = create_app(settings, provider)
    if headers is None:
        headers = {"X-Internal-Api-Key": TEST_KEY, "X-Request-Id": body["requestId"]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/analyses", json=body, headers=headers)


async def test_stub_explicitly_returns_empty_suggestions(analysis_request):
    response = await send(analysis_request)
    assert response.status_code == 200
    assert response.json() == {"requestId": analysis_request["requestId"], "suggestions": []}
    assert response.headers["X-AI-Provider"] == "stub"
    assert response.headers["X-Request-Id"] == analysis_request["requestId"]
    assert response.headers["Cache-Control"] == "no-store"


async def test_backend_contract_example_round_trip(analysis_request, analysis_response):
    provider = StaticProvider(analysis_response)
    response = await send(analysis_request, provider=provider)
    assert response.status_code == 200
    assert response.json() == analysis_response
    assert provider.calls == 1


@pytest.mark.parametrize("kind", ["CREATE", "RESOLVE", "AMBIGUOUS"])
async def test_other_valid_proposals(analysis_request, analysis_response, kind):
    proposal = analysis_response["suggestions"][0]
    if kind == "CREATE":
        proposal.update(
            operation="CREATE", targetItemId=None, expectedVersion=None, candidateItemIds=[]
        )
    elif kind == "RESOLVE":
        proposal["operation"] = "RESOLVE"
    else:
        proposal.update(
            targetItemId=None,
            expectedVersion=None,
            candidateItemIds=[analysis_request["candidates"][0]["itemId"]],
        )
    response = await send(analysis_request, provider=StaticProvider(analysis_response))
    assert response.status_code == 200
    assert response.json() == analysis_response


@pytest.mark.parametrize("key", [None, "", "wrong-key"])
async def test_internal_authentication_required(analysis_request, key):
    provider = StaticProvider({})
    headers = {"X-Request-Id": analysis_request["requestId"]}
    if key is not None:
        headers["X-Internal-Api-Key"] = key
    response = await send(analysis_request, provider=provider, headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert provider.calls == 0


async def test_request_id_must_match_before_calling_provider(analysis_request):
    provider = StaticProvider({})
    response = await send(
        analysis_request,
        provider=provider,
        headers={"X-Internal-Api-Key": TEST_KEY, "X-Request-Id": str(uuid4())},
    )
    assert response.status_code == 400
    assert provider.calls == 0


@pytest.mark.parametrize("header_value", [None, "not-a-uuid"])
async def test_request_id_header_is_required_uuid(analysis_request, header_value):
    headers = {"X-Internal-Api-Key": TEST_KEY}
    if header_value is not None:
        headers["X-Request-Id"] = header_value
    assert (await send(analysis_request, headers=headers)).status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requestId", "invalid"),
        ("careSpaceId", None),
        ("recordId", None),
        ("content", ""),
        ("content", "   "),
        ("content", 123),
        pytest.param("content", "가" * 10001, id="content-too-long"),
        pytest.param("content", "😀" * 5001, id="content-utf16-too-long"),
        ("recordedAt", "2026-09-26T12:00:00"),
        ("recordedAt", 1790414400),
        ("recordedAt", "1790414400"),
        ("timezone", "Not/A-Timezone"),
        ("timezone", "../invalid"),
        ("candidates", None),
        ("unrecognizedField", "private-source-content"),
    ],
)
async def test_invalid_requests_do_not_reach_provider(analysis_request, field, value):
    provider = StaticProvider({})
    analysis_request[field] = value
    headers = {
        "X-Internal-Api-Key": TEST_KEY,
        "X-Request-Id": "00000000-0000-0000-0000-000000000001",
    }
    response = await send(analysis_request, provider=provider, headers=headers)
    assert response.status_code == 422
    assert response.json() == {
        "code": "INVALID_REQUEST",
        "message": "요청 형식과 필수 항목을 확인해주세요.",
    }
    assert provider.calls == 0
    assert "private-source-content" not in response.text


@pytest.mark.parametrize("version", [-1, True, "2", 2.5, 9223372036854775808])
async def test_candidate_version_must_match_java_long(analysis_request, version):
    analysis_request["candidates"][0]["version"] = version
    assert (await send(analysis_request)).status_code == 422


async def test_unique_candidate_identifiers(analysis_request):
    analysis_request["candidates"].append(deepcopy(analysis_request["candidates"][0]))
    assert (await send(analysis_request)).status_code == 422


async def test_candidate_count_limit(analysis_request):
    candidate = analysis_request["candidates"][0]
    analysis_request["candidates"] = [{**candidate, "itemId": str(uuid4())} for _ in range(51)]
    assert (await send(analysis_request)).status_code == 422


async def test_utf16_boundary_remains_compatible_with_java(analysis_request):
    analysis_request["content"] = "😀" * 5000
    assert (await send(analysis_request)).status_code == 200


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("targetItemId", "00000000-0000-0000-0000-000000000099"),
        ("expectedVersion", 99),
        ("expectedVersion", True),
        ("type", "TASK"),
        ("evidence", "private-hallucinated-evidence"),
        ("evidence", "  "),
        ("operation", "DELETE"),
        ("title", "😀" * 101),
        ("changes", {"date": 123}),
        ("changes", {"": "value"}),
        ("changes", {str(i): "value" for i in range(11)}),
        ("unrecognizedField", "private-provider-data"),
    ],
)
async def test_invalid_provider_suggestions_are_sanitized(
    analysis_request, analysis_response, field, value
):
    analysis_response["suggestions"][0][field] = value
    provider = StaticProvider(analysis_response)
    response = await send(analysis_request, provider=provider)
    assert response.status_code == 502
    assert response.json()["code"] == "INVALID_AI_RESPONSE"
    assert "private-" not in response.text
    assert provider.calls == 1


@pytest.mark.parametrize("case", ["wrong-request", "too-many", "missing-field", "wrong-shape"])
async def test_invalid_provider_envelopes(analysis_request, analysis_response, case):
    if case == "wrong-request":
        analysis_response["requestId"] = str(uuid4())
    elif case == "too-many":
        analysis_response["suggestions"] *= 31
    elif case == "missing-field":
        del analysis_response["suggestions"][0]["expectedVersion"]
    else:
        analysis_response = {"unexpected": "private-provider-data"}
    response = await send(analysis_request, provider=StaticProvider(analysis_response))
    assert response.status_code == 502
    assert response.json()["code"] == "INVALID_AI_RESPONSE"


@pytest.mark.parametrize("case", ["unknown", "wrong-type", "duplicate", "missing"])
async def test_ambiguous_candidates_must_be_valid(analysis_request, analysis_response, case):
    proposal = analysis_response["suggestions"][0]
    known_id = analysis_request["candidates"][0]["itemId"]
    proposal.update(targetItemId=None, expectedVersion=None, candidateItemIds=[known_id])
    if case == "unknown":
        proposal["candidateItemIds"] = [str(uuid4())]
    elif case == "wrong-type":
        proposal["type"] = "TASK"
    elif case == "duplicate":
        proposal["candidateItemIds"] = [known_id, known_id]
    else:
        proposal["candidateItemIds"] = []
    response = await send(analysis_request, provider=StaticProvider(analysis_response))
    assert response.status_code == 502


async def test_create_cannot_target_existing_item(analysis_request, analysis_response):
    analysis_response["suggestions"][0]["operation"] = "CREATE"
    response = await send(analysis_request, provider=StaticProvider(analysis_response))
    assert response.status_code == 502


async def test_provider_timeout_cancels_without_retry(analysis_request):
    class SlowProvider:
        calls = 0
        cancelled = False

        async def analyze(self, request):
            self.calls += 1
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    provider = SlowProvider()
    settings = Settings(
        _env_file=None,
        app_env="test",
        ai_provider="stub",
        ai_internal_api_key=TEST_KEY,
        analysis_timeout_seconds=0.01,
    )
    response = await send(analysis_request, provider=provider, settings=settings)
    assert response.status_code == 504
    assert response.json()["code"] == "AI_TIMEOUT"
    assert provider.calls == 1
    assert provider.cancelled is True


async def test_provider_exceptions_never_expose_private_message(analysis_request, caplog):
    class FailedProvider:
        async def analyze(self, request):
            raise RuntimeError("private-care-record and provider-secret")

    response = await send(analysis_request, provider=FailedProvider())
    assert response.status_code == 502
    assert response.json()["code"] == "AI_UNAVAILABLE"
    assert "private-care-record" not in response.text + caplog.text
    assert "provider-secret" not in response.text + caplog.text


async def test_disabled_provider_returns_explicit_unavailable_status(analysis_request):
    settings = Settings(
        _env_file=None, app_env="test", ai_provider="disabled", ai_internal_api_key=TEST_KEY
    )
    response = await send(analysis_request, settings=settings)
    assert response.status_code == 503
    assert response.json()["code"] == "AI_NOT_CONFIGURED"


async def test_openapi_describes_camel_case_contract_and_internal_authentication():
    settings = Settings(_env_file=None, app_env="test", ai_provider="stub")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        schema = (await client.get("/openapi.json")).json()
        assert (await client.get("/health/ready")).status_code == 200
    operation = schema["paths"]["/v1/analyses"]["post"]
    assert operation["security"] == [{"APIKeyHeader": []}]
    assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-Internal-Api-Key"
    assert set(schema["components"]["schemas"]["AnalysisRequest"]["required"]) == {
        "requestId",
        "careSpaceId",
        "recordId",
        "content",
        "recordedAt",
        "timezone",
        "candidates",
    }
