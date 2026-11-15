"""SQLite receipt helpers for scenarios and regression tests."""

import asyncio
import sqlite3
from contextlib import closing

from pydantic import SecretBytes

from app.errors import AnalysisFailure
from app.v2.comparison import ComparisonUnavailable, RequestDigester, canonicalize_v2
from app.v2.receipts import ReceiptGate, SQLiteReceiptStore
from app.v2.services import AnalysisService
from app.v2.validation import parse_request
from tests.support.v2_fixtures import raw

KEY = b"synthetic-receipt-test-key-only-001"
RULE = "fixture-rule-1"
KEY_VERSION = "fixture-key-1"


async def analyze_raw(instance, payload):
    return await instance.analyze_request(parse_request(payload))


def digester(**overrides):
    config = {
        "keys": {KEY_VERSION: SecretBytes(KEY)},
        "active_key_version": KEY_VERSION,
        "active_rule_version": RULE,
        "rules": {RULE: canonicalize_v2},
    }
    return RequestDigester(**(config | overrides))


def store_at(path, *, initialize=False, lock_timeout_seconds=2):
    store = SQLiteReceiptStore(path, lock_timeout_seconds=lock_timeout_seconds)
    if initialize:
        store.initialize()
    return store


def rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT * FROM receipts").fetchall()


class FakeProvider:
    def __init__(self, config=None):
        self.config = config or {"mode": "RETURN", "bodyFile": "payloads/responses/update.json"}
        self.calls = 0

    async def analyze(self, request):
        self.calls += 1
        if self.config["mode"] == "UNAVAILABLE":
            raise RuntimeError("sensitive-provider-detail")
        if self.config["mode"] == "TIMEOUT":
            await asyncio.Event().wait()
        return raw(self.config["bodyFile"])


def service(store, provider, comparison=None, **kwargs):
    return AnalysisService(
        receipts=ReceiptGate(store, comparison or digester()), provider=provider, **kwargs
    )


async def submit(instance, provider, payload):
    before = provider.calls
    try:
        result = await analyze_raw(instance, payload)
        outcome = {"httpStatus": 200, "body": result.model_dump(mode="json")}
    except AnalysisFailure as exc:
        assert exc.message.strip()
        outcome = {"httpStatus": exc.status_code, "code": exc.code}
    except ComparisonUnavailable as exc:
        assert not hasattr(exc, "status_code") and not hasattr(exc, "code")
        outcome = {"outcome": "BLOCKED"}
    return outcome | {"providerCalls": provider.calls - before}
