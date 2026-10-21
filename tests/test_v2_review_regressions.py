"""Reproduce PR #14 review findings without changing shared fixture expectations."""

import asyncio
import json
import threading
from uuid import uuid4

import pytest
from pydantic import SecretBytes
from test_v2_http import KEY, Provider, assert_outcome, build, client, headers, raw, row_count
from test_v2_http_resources import enabled_settings, scope

from app.config import Settings
from app.main import create_app
from app.v2.config import V2Settings
from app.v2.diagnostics import FailureEvent, failure_counts
from app.v2.resources import Capacity
from app.v2.schemas import AnalysisResponse
from app.v2.transport import V2Transport


@pytest.mark.anyio
async def test_disabled_v2_unused_shared_key_does_not_stop_v1(analysis_request):
    app = create_app(
        Settings(_env_file=None, app_env="test", ai_provider="stub", ai_internal_api_key=KEY),
        v2_settings=V2Settings(_env_file=None, hmac_keys={"unused": SecretBytes(KEY)}),
    )
    auth = {"X-Internal-Api-Key": KEY.decode()}
    async with client(app) as http:
        assert (await http.get("/health/ready")).status_code == 200
        response = await http.post(
            "/v1/analyses",
            json=analysis_request,
            headers=auth | {"X-Request-Id": analysis_request["requestId"]},
        )
        assert response.status_code == 200
        assert response.json()["suggestions"] == []
        assert_outcome(
            await http.get("/health/ready/v2", headers=auth),
            {"httpStatus": 503, "code": "AI_NOT_CONFIGURED"},
        )


def test_v2_stub_requires_explicit_environment_instead_of_default_local(tmp_path):
    with pytest.raises(ValueError, match="explicit APP_ENV"):
        create_app(
            Settings(_env_file=None),
            v2_settings=enabled_settings(tmp_path / "unused.sqlite3"),
        )


@pytest.mark.parametrize("environment", ["local", "test"])
def test_v2_stub_allows_explicit_local_or_test_environment(environment, tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", environment)
    app = create_app(
        Settings(_env_file=None),
        v2_settings=enabled_settings(tmp_path / "unused.sqlite3"),
    )
    assert app.state.v2_runtime.service is not None


@pytest.mark.anyio
async def test_disconnect_monitor_starts_only_after_all_body_chunks(tmp_path, monkeypatch):
    from app.v2 import api

    provider = Provider()
    app, instance = build(tmp_path, provider=provider)
    payload = raw()
    chunks = [payload[:1], payload[1:37], payload[37:]]
    consumed = 0
    reading = False
    monitored = False
    original_monitor = api._wait_disconnect

    async def receive():
        nonlocal consumed, reading
        assert not reading, "ASGI receive must have only one consumer"
        reading = True
        try:
            await asyncio.sleep(0)
            if consumed < len(chunks):
                chunk = chunks[consumed]
                consumed += 1
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": consumed < len(chunks),
                }
            await asyncio.Event().wait()
        finally:
            reading = False

    async def monitor(request):
        nonlocal monitored
        assert consumed == len(chunks)
        monitored = True
        await original_monitor(request)

    sent = []

    async def send(message):
        sent.append(message)

    monkeypatch.setattr(api, "_wait_disconnect", monitor)
    await asyncio.wait_for(app(scope(), receive, send), 2)
    assert monitored and consumed == len(chunks)
    assert sent[0]["status"] == 200
    assert provider.calls == row_count(instance) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["app_before_headers", "app_after_headers", "send"])
async def test_transport_does_not_swallow_unrelated_timeouts(source):
    error = TimeoutError("synthetic-private-timeout")
    capacity = Capacity(1)
    request_scope = scope() | {"v2.admission": capacity.acquire()}
    observed = []
    before = failure_counts()

    async def wrapped(scope, receive, send):
        if source == "app_before_headers":
            raise error
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise error

    async def send(message):
        if source == "send":
            raise error
        observed.append(message)

    with pytest.raises(TimeoutError) as caught:
        await V2Transport(wrapped)(request_scope, asyncio.Queue().get, send)
    assert caught.value is error
    assert capacity.active == 0
    event = (FailureEvent.HTTP_SEND_TIMEOUT.value, "rejected")
    assert failure_counts().get(event, 0) == before.get(event, 0)


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", [None, "request_id", "title", "count", "nested_type"])
async def test_provider_response_models_are_fully_revalidated(mutation, tmp_path):
    model = AnalysisResponse.model_validate(json.loads(raw("payloads/responses/update.json")))
    if mutation == "request_id":
        model.request_id = uuid4()
    elif mutation == "title":
        model.suggestions[0].title = "x" * 501
    elif mutation == "count":
        model.suggestions *= 31
    elif mutation == "nested_type":
        model.suggestions[0].expected_version = True

    class ModelProvider:
        calls = 0

        async def analyze(self, request):
            self.calls += 1
            return model

    provider = ModelProvider()
    app, instance = build(tmp_path, provider=provider)
    async with client(app) as http:
        response = await http.post("/v2/analyses", content=raw(), headers=headers())
    if mutation is None:
        assert response.status_code == 200
        assert response.json() == model.model_dump(mode="json", by_alias=True)
    else:
        assert_outcome(response, {"httpStatus": 502, "code": "INVALID_AI_RESPONSE"})
    assert provider.calls == row_count(instance) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["/v2/analyses", "/health/ready/v2"])
async def test_storage_capacity_reports_overload_and_recovers(endpoint, tmp_path):
    provider = Provider()
    app, instance = build(tmp_path, provider=provider)
    loop = asyncio.get_running_loop()
    started = [asyncio.Event() for _ in range(4)]
    release = threading.Event()

    def blocked(index):
        loop.call_soon_threadsafe(started[index].set)
        assert release.wait(5), "test worker release was not signalled"

    callers = [asyncio.create_task(instance.storage.run(blocked, i, budget=5)) for i in range(4)]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 2)
        async with client(app) as http:
            if endpoint == "/v2/analyses":
                response = await http.post(endpoint, content=raw(), headers=headers())
            else:
                response = await http.get(endpoint, headers=headers())
        assert_outcome(response, {"httpStatus": 503, "code": "AI_OVERLOADED"})
        assert provider.calls == row_count(instance) == 0
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*callers), 2)
    assert instance.storage.capacity.active == 0
    async with client(app) as http:
        assert (await http.get("/health/ready/v2", headers=headers())).status_code == 200
        assert (
            await http.post("/v2/analyses", content=raw(), headers=headers())
        ).status_code == 200
    assert provider.calls == row_count(instance) == 1
