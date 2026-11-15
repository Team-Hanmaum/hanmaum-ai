"""Shared application and ASGI helpers for v2 HTTP tests."""

import asyncio
import json
from contextlib import closing
from uuid import UUID

import httpx
from pydantic import SecretBytes

from app.config import Settings
from app.main import create_app
from app.v2.comparison import RequestDigester
from app.v2.config import V2Settings
from app.v2.receipts import ReceiptGate, SQLiteReceiptStore
from app.v2.services import AnalysisService
from tests.support.v2_fixtures import body, raw

KEY = b"synthetic-http-hmac-key-for-tests-only"
AUTH = "fixture-only-not-a-secret"


def headers(payload=None):
    return {
        "Content-Type": "application/json",
        "X-Internal-Api-Key": AUTH,
        "X-Request-Id": json.loads(payload or raw())["requestId"],
    }


def normalize(value, key=None):
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, key) for v in value]
    if value is not None and key in {
        "requestId",
        "targetItemId",
        "candidateItemIds",
        "itemId",
        "basisIds",
    }:
        return str(UUID(value))
    return value


class Provider:
    def __init__(self, spec=None):
        self.spec = spec or {"mode": "RETURN", "bodyFile": "payloads/responses/update.json"}
        self.calls = 0

    async def analyze(self, request):
        self.calls += 1
        mode = self.spec["mode"]
        if mode == "UNAVAILABLE":
            raise RuntimeError("synthetic-private-provider-error")
        if mode == "TIMEOUT":
            await asyncio.Event().wait()
        return body(self.spec)


def build(tmp_path, *, provider=None, settings=None, store=None, **service_options):
    if store is None:
        store = SQLiteReceiptStore(tmp_path / "receipts.sqlite3", lock_timeout_seconds=0.05)
        store.initialize()
    digester = RequestDigester(keys={"test": SecretBytes(KEY)}, active_key_version="test")
    instance = AnalysisService(
        receipts=ReceiptGate(store, digester), provider=provider, **service_options
    )
    config = settings or V2Settings(
        _env_file=None,
        provider="stub",
        receipt_db_path=store.path,
        hmac_keys={"test": SecretBytes(KEY)},
        active_hmac_key_version="test",
    )
    app = create_app(
        Settings(_env_file=None, app_env="test", ai_internal_api_key=AUTH),
        v2_settings=config,
        v2_service=instance,
    )
    return app, instance


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def row_count(instance):
    with closing(instance.receipts.store._connect()) as connection:
        return connection.execute("SELECT count(*) FROM receipts").fetchone()[0]


def assert_outcome(response, expected):
    assert response.status_code == expected["httpStatus"], response.text
    assert response.headers["cache-control"] == "no-store"
    assert "retry-after" not in response.headers
    assert len(response.content) <= 524_288
    if "code" in expected:
        assert response.json()["code"] == expected["code"]
        assert response.json()["message"].strip()
        assert set(response.json()) == {"code", "message"}
    else:
        assert normalize(response.json()) == normalize(json.loads(body(expected)))


class StubbornProvider:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def analyze(self, request):
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
        return {"requestId": str(request.request_id), "suggestions": []}


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


def enabled_settings(path):
    return V2Settings(
        _env_file=None,
        provider="stub",
        receipt_db_path=path,
        hmac_keys={"test": SecretBytes(KEY)},
        active_hmac_key_version="test",
    )
