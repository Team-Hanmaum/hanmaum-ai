"""HTTP transport, worker lifetime, setup and regression checks outside shared goldens."""

import asyncio
import json
import socket
import threading
import time
from uuid import UUID

import pytest
import uvicorn
from pydantic import SecretBytes, ValidationError
from test_v2_http import (
    AUTH,
    KEY,
    Provider,
    StubbornProvider,
    assert_outcome,
    build,
    client,
    generated,
    headers,
    raw,
    row_count,
)

from app.config import Settings
from app.errors import AnalysisFailure
from app.main import create_app
from app.v2.config import V2Settings
from app.v2.limits import REQUEST_BYTES
from app.v2.receipts import SQLiteReceiptStore
from app.v2.resources import Capacity, Workers
from app.v2.transport import V2Transport
from app.v2.validation import parse_request, validate_response


def scope(payload=None):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v2/analyses",
        "raw_path": b"/v2/analyses",
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": ("test", 1),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers(payload).items()],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change,status,code",
    [
        ({"X-Internal-Api-Key": "wrong", "Content-Length": "999999999"}, 401, "UNAUTHORIZED"),
        ({"Content-Length": "9999999999999999999999999"}, 413, "REQUEST_TOO_LARGE"),
        ({"Content-Length": "-1"}, 422, "INVALID_REQUEST"),
        ({"Content-Type": "text/plain"}, 422, "INVALID_REQUEST"),
        ({"Content-Encoding": "gzip"}, 422, "INVALID_REQUEST"),
        ({"X-Request-Id": "invalid"}, 422, "INVALID_REQUEST"),
    ],
)
async def test_early_rejections_never_register_or_call(change, status, code, tmp_path):
    provider = Provider()
    app, instance = build(tmp_path, provider=provider)
    async with client(app) as http:
        response = await http.post("/v2/analyses", content=raw(), headers=headers() | change)
    assert_outcome(response, {"httpStatus": status, "code": code})
    assert provider.calls == row_count(instance) == 0
    assert app.state.v2_runtime.admissions.active == 0


@pytest.mark.anyio
async def test_oversized_request_is_rejected_before_json_decode(tmp_path, monkeypatch):
    provider = Provider()
    app, instance = build(tmp_path, provider=provider)

    def forbidden_parse(*args):
        pytest.fail("oversized raw bytes must not reach the decoder")

    monkeypatch.setattr("app.v2.api.parse_request", forbidden_parse)
    async with client(app) as http:
        response = await http.post(
            "/v2/analyses",
            content=b" " * (REQUEST_BYTES + 1),
            headers=headers() | {"Content-Length": "1"},
        )
    assert_outcome(response, {"httpStatus": 413, "code": "REQUEST_TOO_LARGE"})
    assert provider.calls == row_count(instance) == 0


@pytest.mark.anyio
async def test_body_receive_has_its_own_deadline(tmp_path):
    provider = Provider()
    app, instance = build(
        tmp_path, provider=provider, settings=V2Settings(_env_file=None, body_timeout_seconds=0.02)
    )

    async def slow_body():
        yield b"{"
        await asyncio.Event().wait()

    async with client(app) as http:
        response = await http.post("/v2/analyses", content=slow_body(), headers=headers())
    assert_outcome(response, {"httpStatus": 504, "code": "AI_TIMEOUT"})
    assert provider.calls == row_count(instance) == 0


@pytest.mark.anyio
async def test_admission_limits_body_reading_independently_of_provider_slots(tmp_path):
    provider = Provider()
    app, instance = build(tmp_path, provider=provider)
    runtime = app.state.v2_runtime
    ready = [asyncio.Event() for _ in range(4)]
    release = asyncio.Event()

    async def body(index):
        ready[index].set()
        await release.wait()
        yield raw()

    async with client(app) as http:
        callers = [
            asyncio.create_task(http.post("/v2/analyses", content=body(i), headers=headers()))
            for i in range(4)
        ]
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in ready)), 2)
        assert runtime.admissions.active == 4 and instance.slots.active == 0
        response = await http.post("/v2/analyses", content=raw(), headers=headers())
        assert_outcome(response, {"httpStatus": 503, "code": "AI_OVERLOADED"})
        assert provider.calls == row_count(instance) == 0
        for caller in callers:
            caller.cancel()
        await asyncio.gather(*callers, return_exceptions=True)
        assert runtime.admissions.active == 0


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["parse", "claim"])
async def test_late_worker_retains_capacity_and_cannot_start_provider(stage, tmp_path, monkeypatch):
    provider = Provider()
    app, instance = build(
        tmp_path,
        provider=provider,
        worker_timeout_seconds=0.02,
        settings=V2Settings(_env_file=None, worker_timeout_seconds=0.02),
    )
    release = threading.Event()
    started = threading.Event()
    original = parse_request if stage == "parse" else instance.receipts.claim

    def blocking(value):
        started.set()
        if not release.wait(3):
            raise RuntimeError("test worker release was not signalled")
        return original(value)

    if stage == "parse":
        monkeypatch.setattr("app.v2.api.parse_request", blocking)
    else:
        monkeypatch.setattr(instance.receipts, "claim", blocking)
    try:
        async with client(app) as http:
            response = await http.post("/v2/analyses", content=raw(), headers=headers())
        assert started.is_set()
        assert_outcome(
            response,
            {
                "httpStatus": 504 if stage == "parse" else 503,
                "code": "AI_TIMEOUT" if stage == "parse" else "RECEIPT_STORE_UNAVAILABLE",
            },
        )
        assert app.state.v2_runtime.admissions.active == 1
        assert instance.slots.active == int(stage == "claim")
        assert provider.calls == 0
        workers = instance.cpu if stage == "parse" else instance.storage
        assert workers.capacity.active == 1
        tasks = set(workers.tasks)
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 2)
    assert instance.slots.active == app.state.v2_runtime.admissions.active == 0
    assert provider.calls == 0 and row_count(instance) == int(stage == "claim")


@pytest.mark.anyio
async def test_worker_pool_does_not_queue_or_release_a_running_thread_on_caller_cancel():
    workers = Workers(1)
    capacity = Capacity(1)
    owner = capacity.acquire()
    started = threading.Event()
    release = threading.Event()

    def work():
        started.set()
        release.wait(3)

    caller = asyncio.create_task(workers.run(work, budget=1, leases=(owner,)))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        owner.release()
        assert workers.capacity.active == capacity.active == 1
        with pytest.raises(AnalysisFailure) as caught:
            await workers.run(lambda: None)
        assert caught.value.code == "AI_OVERLOADED"
        tasks = set(workers.tasks)
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 2)
    assert workers.capacity.active == capacity.active == 0


@pytest.mark.anyio
async def test_network_disconnect_cancels_provider_but_preserves_receipt(tmp_path):
    provider = StubbornProvider()
    app, instance = build(tmp_path, provider=provider)
    messages = asyncio.Queue()
    await messages.put({"type": "http.request", "body": raw(), "more_body": False})
    sent = []

    async def send(message):
        sent.append(message)

    caller = asyncio.create_task(app(scope(), messages.get, send))
    await asyncio.wait_for(provider.started.wait(), 2)
    await messages.put({"type": "http.disconnect"})
    await asyncio.wait_for(caller, 2)
    await asyncio.wait_for(provider.cancelled.wait(), 2)
    assert sent == []
    assert row_count(instance) == 1 and instance.slots.active == 1
    tasks = set(instance.tasks)
    provider.release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 2)
    assert instance.slots.active == app.state.v2_runtime.admissions.active == 0


@pytest.mark.anyio
async def test_response_backpressure_retains_admission_then_stops_send(tmp_path):
    app, instance = build(tmp_path, provider=Provider())
    # Install the same production middleware with a shorter test budget.
    app.user_middleware.clear()
    app.add_middleware(V2Transport, send_budget=0.02)
    messages = asyncio.Queue()
    await messages.put({"type": "http.request", "body": raw(), "more_body": False})
    observed = []

    async def slow_send(message):
        observed.append(message["type"])
        assert app.state.v2_runtime.admissions.active == 1
        await asyncio.Event().wait()

    await asyncio.wait_for(app(scope(), messages.get, slow_send), 2)
    assert observed == ["http.response.start"]
    assert app.state.v2_runtime.admissions.active == 0
    assert row_count(instance) == 1


@pytest.mark.anyio
async def test_total_deadline_clips_provider_wait_and_keeps_late_task_slot(tmp_path):
    provider = StubbornProvider()
    settings = V2Settings(_env_file=None, total_timeout_seconds=0.3, worker_timeout_seconds=0.05)
    app, instance = build(
        tmp_path, provider=provider, settings=settings, worker_timeout_seconds=0.05
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    async with client(app) as http:
        response = await http.post("/v2/analyses", content=raw(), headers=headers())
    assert_outcome(response, {"httpStatus": 504, "code": "AI_TIMEOUT"})
    assert loop.time() - started < 1
    assert row_count(instance) == 1 and instance.slots.active == 1
    tasks = set(instance.tasks)
    provider.release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 2)


@pytest.mark.anyio
async def test_response_is_rechecked_after_uuid_normalization(tmp_path, caplog):
    # Input is within the string total; declared compact UUIDs expand on output.
    response = json.loads(generated("response-stringUnits-at"))

    def compact(value, key=None):
        if isinstance(value, dict):
            return {k: compact(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [compact(v, key) for v in value]
        if value is not None and key in {
            "requestId",
            "targetItemId",
            "candidateItemIds",
            "itemId",
            "basisIds",
        }:
            return UUID(value).hex
        return value

    response = compact(response)
    before = len(json.dumps(response))
    # Refill exactly the characters removed by compacting the response request UUID.
    response["suggestions"][-1]["changes"]["description"] += "a" * 4
    assert len(json.dumps(response)) == before + 4
    provider = Provider()

    async def analyze(request):
        provider.calls += 1
        return response

    provider.analyze = analyze
    app, instance = build(tmp_path, provider=provider)
    payload = generated("request-minimal")
    # Ensure rejection is caused by final serialization, not the raw provider boundary.
    validate_response(parse_request(payload), response)
    async with client(app) as http:
        result = await http.post("/v2/analyses", content=payload, headers=headers(payload))
    assert_outcome(result, {"httpStatus": 502, "code": "INVALID_AI_RESPONSE"})
    assert row_count(instance) == provider.calls == 1
    assert "provider.invalid_response" in caplog.text


@pytest.mark.parametrize(
    "values",
    [
        {"provider_timeout_seconds": 25.1},
        {"total_timeout_seconds": 26.1},
        {"body_timeout_seconds": 2.1},
        {"worker_timeout_seconds": 1.1},
        {"provider_timeout_seconds": float("nan")},
        {"provider": "stub"},
    ],
)
def test_invalid_resource_or_enabled_configuration_is_rejected(values):
    with pytest.raises(ValidationError):
        V2Settings(_env_file=None, **values)


def enabled_settings(path):
    return V2Settings(
        _env_file=None,
        provider="stub",
        receipt_db_path=path,
        hmac_keys={"test": SecretBytes(KEY)},
        active_hmac_key_version="test",
    )


@pytest.mark.anyio
async def test_v2_readiness_requires_explicit_store_initialization_and_leaves_v1_unchanged(
    tmp_path,
):
    path = tmp_path / "new.sqlite3"
    app = create_app(
        Settings(_env_file=None, app_env="test", ai_provider="stub", ai_internal_api_key=AUTH),
        v2_settings=enabled_settings(path),
    )
    async with client(app) as http:
        assert (await http.get("/health")).status_code == 200
        assert (await http.get("/health/ready")).status_code == 200
        not_ready = await http.get("/health/ready/v2", headers=headers())
        assert_outcome(not_ready, {"httpStatus": 503, "code": "RECEIPT_STORE_UNAVAILABLE"})
        assert not path.exists()
        SQLiteReceiptStore(path, lock_timeout_seconds=0.05).initialize()
        assert (await http.get("/health/ready/v2", headers=headers())).status_code == 200
        response = await http.post("/v2/analyses", content=raw(), headers=headers())
        assert response.status_code == 200 and response.json()["suggestions"] == []
        assert response.headers["x-ai-provider"] == "stub"


def test_internal_auth_and_receipt_hmac_keys_cannot_be_reused(tmp_path):
    with pytest.raises(ValueError, match="separate"):
        create_app(
            Settings(_env_file=None, app_env="test", ai_internal_api_key=KEY.decode()),
            v2_settings=enabled_settings(tmp_path / "unused.sqlite3"),
        )


def test_production_cannot_enable_v2_stub(tmp_path):
    with pytest.raises(ValueError, match="restricted"):
        create_app(
            Settings(
                _env_file=None,
                app_env="prod",
                ai_internal_api_key="synthetic-production-key-minimum-32",
            ),
            v2_settings=enabled_settings(tmp_path / "unused.sqlite3"),
        )


def test_real_http_server_with_stub_and_persisted_receipt(tmp_path):
    """Exercise Uvicorn, startup/shutdown and the real socket without any LLM/BE calls."""
    import httpx

    path = tmp_path / "wire.sqlite3"
    SQLiteReceiptStore(path, lock_timeout_seconds=0.05).initialize()
    app = create_app(
        Settings(_env_file=None, app_env="test", ai_provider="stub", ai_internal_api_key=AUTH),
        v2_settings=enabled_settings(path),
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="critical"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False
            ) as http:
                assert http.get("/health").status_code == 200
                assert http.get("/health/ready").status_code == 200
                assert http.get("/health/ready/v2", headers=headers()).status_code == 200
                response = http.post("/v2/analyses", content=raw(), headers=headers())
                assert response.status_code == 200
                assert response.json() == {
                    "requestId": headers()["X-Request-Id"],
                    "suggestions": [],
                }
                duplicate = http.post("/v2/analyses", content=raw(), headers=headers())
                assert_outcome(duplicate, {"httpStatus": 409, "code": "REQUEST_ALREADY_RECEIVED"})
                oversized = http.post(
                    "/v2/analyses", content=iter([b" " * 8192] * 129), headers=headers()
                )
                assert_outcome(oversized, {"httpStatus": 413, "code": "REQUEST_TOO_LARGE"})
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        assert not thread.is_alive()


def test_explicit_provisioning_cli_preserves_existing_records(tmp_path, monkeypatch, capsys):
    from app.v2.initialize_receipts import main

    app, instance = build(tmp_path, provider=Provider())
    instance.receipts.claim(parse_request(raw()))
    monkeypatch.setattr("sys.argv", ["initialize_receipts", str(instance.receipts.store.path)])
    main()
    assert "existing records preserved" in capsys.readouterr().out
    assert row_count(instance) == 1
