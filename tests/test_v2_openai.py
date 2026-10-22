"""Mock the paid boundary, then exercise the actual adapter, service and HTTP endpoint."""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict

import httpx
import pytest
from pydantic import ValidationError
from test_v2_http import AUTH, KEY, ROOT, build, client, headers, raw, row_count

from app.config import LOCAL_API_KEY, Settings
from app.main import create_app
from app.v2.config import V2Settings
from app.v2.openai_provider import ENVELOPE_BYTES, OpenAIProvider
from app.v2.provider_errors import InvalidProviderOutput
from app.v2.structured_output import output_schema, translate_output
from app.v2.validation import parse_request, validate_response

SECRET = "synthetic-openai-key-never-real"


def settings(tmp_path, **overrides):
    return V2Settings(
        _env_file=None,
        **(
            {
                "provider": "openai",
                "openai_api_key": SECRET,
                "receipt_db_path": tmp_path / "receipts.sqlite3",
                "hmac_keys": {"test": KEY},
                "active_hmac_key_version": "test",
            }
            | overrides
        ),
    )


def wire(body=None):
    data = deepcopy(
        body if body is not None else json.loads(raw("payloads/responses/resolve.json"))
    )
    data.pop("requestId")
    for suggestion in data["suggestions"]:
        suggestion["changes"] = [{"field": k, "value": v} for k, v in suggestion["changes"].items()]
    return data


def envelope(value=None):
    return {
        "status": "completed",
        "error": None,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(value or wire())}],
            },
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 10},
            "output_tokens_details": {"reasoning_tokens": 20},
        },
    }


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def response(value=None, *, status=200, stream=None, extra_headers=None):
    return httpx.Response(
        status,
        headers={"content-type": "application/json"} | (extra_headers or {}),
        stream=stream or Stream([json.dumps(value or envelope()).encode()]),
    )


@pytest.mark.anyio
async def test_real_adapter_request_and_http_duplicate_preserve_v1(tmp_path, analysis_request):
    sent = []

    def send(request):
        sent.append(request)
        return response()

    config = settings(tmp_path)
    provider = OpenAIProvider(config, transport=httpx.MockTransport(send))
    app, service = build(tmp_path, provider=provider, settings=config)
    async with client(app) as http:
        result = await http.post("/v2/analyses", content=raw(), headers=headers())
        assert result.status_code == 200
        assert result.json() == json.loads(raw("payloads/responses/resolve.json"))
        assert result.headers["x-ai-provider"] == "openai"
        repeated = await http.post("/v2/analyses", content=raw(), headers=headers())
        assert repeated.status_code == 409
        assert repeated.json()["code"] == "REQUEST_ALREADY_RECEIVED"
        assert (await http.get("/health/ready/v2", headers=headers())).status_code == 200
    await provider.close()
    assert len(sent) == provider.calls == row_count(service) == 1
    request = sent[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    data = json.loads(request.content)
    assert (data["model"], data["reasoning"], data["service_tier"]) == (
        "gpt-6-luna",
        {"effort": "low"},
        "default",
    )
    assert data["store"] is False and data["stream"] is False
    assert data["tools"] == [] and data["truncation"] == "disabled"
    assert data["max_output_tokens"] == 4096
    original = json.loads(raw())
    model_input = json.loads(data["input"][0]["content"])
    assert model_input == {
        k: original[k] for k in ("content", "recordedAt", "timezone", "candidates")
    }
    assert not {"requestId", "careSpaceId", "recordId"} & model_input.keys()
    assert data["text"]["format"]["strict"] is True
    metrics = asdict(provider.last_observation)
    assert metrics["input_tokens"] == 120 and metrics["reasoning_tokens"] == 20
    assert not any(value in json.dumps(metrics) for value in [SECRET, AUTH, "준비물"])


SUCCESS_CASES = [
    case
    for case in json.loads((ROOT / "cases/static.json").read_bytes())
    if case["expected"]["ai"]["httpStatus"] == 200
]


@pytest.mark.parametrize("case", SUCCESS_CASES, ids=lambda case: case["caseId"])
def test_all_successful_common_cases_round_trip_without_changing_semantics(case):
    request = parse_request(raw(case["request"]["bodyFile"]))
    expected = json.loads(raw(case["expected"]["ai"]["bodyFile"]))
    translated = translate_output(json.dumps(wire(expected)), str(request.request_id))
    assert validate_response(request, translated) == validate_response(request, expected)


def test_schema_has_only_required_closed_objects():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(output_schema())
    assert set(output_schema()["properties"]) == {"suggestions"}


def test_input_preserves_korean_whitespace_and_lone_surrogate(tmp_path):
    data = json.loads(raw())
    data["content"] = "  한국어\n\ud800 "
    request = parse_request(json.dumps(data))
    provider = OpenAIProvider(settings(tmp_path))
    text = provider.request_body(request)["input"][0]["content"]
    assert "한국어" in text
    assert json.loads(text) == {
        k: data[k] for k in ("content", "recordedAt", "timezone", "candidates")
    }
    text.encode("utf-8")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "extra_headers",
    [
        {"content-encoding": "gzip"},
        {"content-type": "text/html"},
        {"content-length": "-1"},
        {"content-length": "not-a-number"},
    ],
)
async def test_invalid_envelope_headers_rejected_before_read(tmp_path, extra_headers):
    stream = Stream([b"sensitive-response"])
    provider = OpenAIProvider(
        settings(tmp_path),
        transport=httpx.MockTransport(
            lambda _: response(stream=stream, extra_headers=extra_headers)
        ),
    )
    with pytest.raises(InvalidProviderOutput):
        await provider.analyze(parse_request(raw()))
    await provider.close()
    assert stream.reads == 0 and stream.closed


@pytest.mark.anyio
async def test_model_text_limit_is_separate_from_envelope_limit(tmp_path):
    value = envelope()
    value["output"][1]["content"][0]["text"] = " " * 524289
    provider = OpenAIProvider(
        settings(tmp_path), transport=httpx.MockTransport(lambda _: response(value))
    )
    with pytest.raises(InvalidProviderOutput):
        await provider.analyze(parse_request(raw()))
    await provider.close()


@pytest.mark.anyio
async def test_decoded_output_limits_still_apply_after_translation(tmp_path):
    value = wire()
    value["suggestions"][0]["changes"][0]["value"] = "가" * 10001
    provider = OpenAIProvider(
        settings(tmp_path), transport=httpx.MockTransport(lambda _: response(envelope(value)))
    )
    app, service = build(tmp_path, provider=provider)
    async with client(app) as http:
        result = await http.post("/v2/analyses", content=raw(), headers=headers())
    await provider.close()
    assert result.status_code == 502 and result.json()["code"] == "INVALID_AI_RESPONSE"
    assert row_count(service) == provider.calls == 1


@pytest.mark.parametrize(
    "change",
    [
        None,
        {},
        [{"field": "answerText", "value": "답"}] * 2,
        [{"field": "answerText", "value": "답", "extra": True}],
    ],
)
def test_translation_rejects_lossy_changes(change):
    value = wire()
    value["suggestions"][0]["changes"] = change
    with pytest.raises(ValueError):
        translate_output(json.dumps(value), str(parse_request(raw()).request_id))


@pytest.mark.anyio
@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 404, 408, 429, 500, 503])
async def test_http_error_body_not_read_and_no_retry_or_credential_redirect(
    tmp_path, status, caplog
):
    stream = Stream([b"sensitive-provider-error" * 200_000])
    provider = OpenAIProvider(
        settings(tmp_path),
        transport=httpx.MockTransport(
            lambda _: response(
                status=status, stream=stream, extra_headers={"location": "https://evil.test"}
            )
        ),
    )
    app, service = build(tmp_path, provider=provider)
    async with client(app) as http:
        result = await http.post("/v2/analyses", content=raw(), headers=headers())
    await provider.close()
    assert (result.status_code, result.json()["code"]) == (502, "AI_UNAVAILABLE")
    assert provider.calls == row_count(service) == 1
    assert stream.reads == 0 and stream.closed
    assert "sensitive-provider-error" not in caplog.text + result.text
    assert SECRET not in caplog.text + result.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind",
    [
        "refusal",
        "incomplete",
        "malformed",
        "duplicate-key",
        "tool",
        "multiple",
        "wrong-role",
        "extra-root",
        "invalid-reference",
        "partial-invalid",
        "null-change",
        "unknown-field",
    ],
)
async def test_invalid_outputs_fail_as_whole_and_keep_receipt(tmp_path, kind, caplog):
    value = envelope()
    if kind == "refusal":
        value["output"][1]["content"] = [{"type": "refusal", "refusal": "sensitive-refusal"}]
    elif kind == "incomplete":
        value["status"] = "incomplete"
        value["incomplete_details"] = {"reason": "max_output_tokens"}
    elif kind == "tool":
        value["output"].append({"type": "function_call", "arguments": "sensitive"})
    elif kind == "multiple":
        value["output"].append(value["output"][1])
    elif kind == "wrong-role":
        value["output"][1]["role"] = "user"
    else:
        output = wire()
        if kind == "extra-root":
            output["requestId"] = "invented"
        elif kind == "invalid-reference":
            output["suggestions"][0]["expectedVersion"] = 99
        elif kind == "partial-invalid":
            output["suggestions"].append(deepcopy(output["suggestions"][0]))
            output["suggestions"][1]["evidence"] = "not-in-source"
        elif kind == "null-change":
            output["suggestions"][0]["changes"][0]["value"] = None
        elif kind == "unknown-field":
            output["suggestions"][0]["changes"][0]["field"] = "injected"
        text = json.dumps(output)
        if kind == "malformed":
            text = "sensitive-not-json"
        if kind == "duplicate-key":
            text = '{"suggestions":[],"suggestions":[]}'
        value["output"][1]["content"][0]["text"] = text
    provider = OpenAIProvider(
        settings(tmp_path), transport=httpx.MockTransport(lambda _: response(value))
    )
    app, service = build(tmp_path, provider=provider)
    async with client(app) as http:
        result = await http.post("/v2/analyses", content=raw(), headers=headers())
        assert result.status_code == 502
        assert result.json()["code"] == "INVALID_AI_RESPONSE"
        assert (
            await http.post("/v2/analyses", content=raw(), headers=headers())
        ).status_code == 409
    await provider.close()
    assert provider.calls == row_count(service) == 1
    assert "sensitive" not in caplog.text + result.text


@pytest.mark.anyio
@pytest.mark.parametrize("header", [False, True])
async def test_oversized_stream_bounded_with_or_without_length(tmp_path, header):
    stream = Stream([b" " * 65536] * (ENVELOPE_BYTES // 65536 + 8))
    provider = OpenAIProvider(
        settings(tmp_path),
        transport=httpx.MockTransport(
            lambda _: response(
                stream=stream,
                extra_headers={"content-length": str(ENVELOPE_BYTES + 1)} if header else {},
            )
        ),
    )
    with pytest.raises(InvalidProviderOutput):
        await provider.analyze(parse_request(raw()))
    await provider.close()
    assert stream.closed
    assert stream.reads == (0 if header else ENVELOPE_BYTES // 65536 + 1)


@pytest.mark.anyio
async def test_cancelled_stream_closes_and_does_not_retry(tmp_path):
    entered = asyncio.Event()

    class Waiting(Stream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"never"

    stream = Waiting([])
    provider = OpenAIProvider(
        settings(tmp_path), transport=httpx.MockTransport(lambda _: response(stream=stream))
    )
    app, service = build(tmp_path, provider=provider, timeout_seconds=0.05)
    async with client(app) as http:
        result = await http.post("/v2/analyses", content=raw(), headers=headers())
    await asyncio.sleep(0.01)
    await provider.close()
    assert entered.is_set() and stream.closed
    assert result.json()["code"] == "AI_TIMEOUT"
    assert provider.calls == row_count(service) == 1
    assert service.slots.active == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error, code",
    [(httpx.ReadTimeout("secret"), "AI_TIMEOUT"), (httpx.ConnectError("secret"), "AI_UNAVAILABLE")],
)
async def test_transport_errors_sanitized(tmp_path, error, code, caplog):
    def send(_):
        raise error

    provider = OpenAIProvider(settings(tmp_path), transport=httpx.MockTransport(send))
    app, _ = build(tmp_path, provider=provider)
    async with client(app) as http:
        result = await http.post("/v2/analyses", content=raw(), headers=headers())
    await provider.close()
    assert result.json()["code"] == code
    assert "secret" not in result.text + caplog.text


def test_openai_configuration_requires_distinct_key_only_when_enabled(tmp_path):
    for key in [None, "", "with space", "line\nbreak", "한글", KEY.decode()]:
        with pytest.raises(ValidationError):
            settings(tmp_path, openai_api_key=key)
    config = settings(tmp_path, openai_api_key=AUTH)
    with pytest.raises(ValueError, match="separate"):
        create_app(
            Settings(_env_file=None, app_env="test", ai_internal_api_key=AUTH), v2_settings=config
        )
    V2Settings(_env_file=None, provider="disabled")
    with pytest.raises(ValidationError):
        settings(tmp_path, service_tier="priority")


@pytest.mark.anyio
async def test_runtime_owns_lazy_client_no_readiness_call_and_closes(tmp_path, analysis_request):
    config = settings(tmp_path)
    app = create_app(
        Settings(_env_file=None, app_env="test", ai_provider="stub", ai_internal_api_key=AUTH),
        v2_settings=config,
    )
    runtime = app.state.v2_runtime
    runtime.service.receipts.store.initialize()
    provider = runtime.owned_provider
    assert isinstance(provider, OpenAIProvider) and provider._client is None
    async with app.router.lifespan_context(app), client(app) as http:
        assert (await http.get("/health/ready/v2", headers=headers())).status_code == 200
        v1 = await http.post(
            "/v1/analyses",
            json=analysis_request,
            headers=headers() | {"X-Request-Id": analysis_request["requestId"]},
        )
        assert v1.status_code == 200 and v1.json()["suggestions"] == []
        assert provider.calls == 0
        connection = provider._get_client()
    assert connection.is_closed


@pytest.mark.parametrize("environment", [None, "local", "test"])
def test_openai_rejects_public_internal_key_even_when_environment_is_omitted(tmp_path, environment):
    options = {"app_env": environment} if environment else {}
    for override in ({}, {"ai_internal_api_key": LOCAL_API_KEY}):
        with pytest.raises(ValueError, match="non-public internal API key"):
            create_app(
                Settings(_env_file=None, **options, **override), v2_settings=settings(tmp_path)
            )
    app = create_app(
        Settings(_env_file=None, **options, ai_internal_api_key=AUTH),
        v2_settings=settings(tmp_path),
    )
    assert app.state.v2_runtime.owned_provider.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status, error, expected_code",
    [
        ("failed", {"code": "server_error", "message": "sensitive-upstream"}, "AI_UNAVAILABLE"),
        (
            "completed",
            {"code": "rate_limit_exceeded", "message": "sensitive-upstream"},
            "AI_UNAVAILABLE",
        ),
        ("failed", None, "INVALID_AI_RESPONSE"),
        ("completed", "sensitive-upstream", "INVALID_AI_RESPONSE"),
        ("completed", {}, "INVALID_AI_RESPONSE"),
        ("incomplete", None, "INVALID_AI_RESPONSE"),
    ],
)
async def test_error_envelope_distinguishes_provider_failure_from_bad_output(
    tmp_path, status, error, expected_code, caplog
):
    value = envelope() | {"status": status, "error": error}
    provider = OpenAIProvider(
        settings(tmp_path), transport=httpx.MockTransport(lambda _: response(value))
    )
    app, service = build(tmp_path, provider=provider)
    async with client(app) as http:
        result = await http.post("/v2/analyses", content=raw(), headers=headers())
        assert result.status_code == 502 and result.json()["code"] == expected_code
        assert (
            await http.post("/v2/analyses", content=raw(), headers=headers())
        ).status_code == 409
    await provider.close()
    assert provider.calls == row_count(service) == 1
    assert "sensitive-upstream" not in result.text + caplog.text
