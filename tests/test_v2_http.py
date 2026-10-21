"""Execute the frozen v2 fixture through FastAPI; never emulate BE persistence."""

import asyncio
import json
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import SecretBytes

from app.config import Settings
from app.main import create_app
from app.v2.comparison import Fingerprint, RequestDigester
from app.v2.config import V2Settings
from app.v2.receipts import Receipt, ReceiptGate, SQLiteReceiptStore
from app.v2.services import AnalysisService
from app.v2.validation import parse_request
from scripts.fixture_payloads import materialize

ROOT = Path(__file__).parent / "fixtures/v2"
KEY = b"synthetic-http-hmac-key-for-tests-only"
AUTH = "fixture-only-not-a-secret"
STATIC = json.loads((ROOT / "cases/static.json").read_bytes())
LIMITS = json.loads((ROOT / "cases/limits.json").read_bytes())
HTTP = {c["caseId"]: c for c in json.loads((ROOT / "cases/http.json").read_bytes())}
GENERATED = json.loads((ROOT / "generated.json").read_bytes())
BE_ONLY = {
    "H-chunked-response-at",
    "H-chunked-response-above",
    "H-unknown-error-422-request-too-large",
    "H-unknown-error-409-request-comparison-unavailable",
    "H-unknown-error-429-ai-overloaded",
    "H-unknown-error-503-unknown-code",
    "H-be-preflight-limit",
    "H-be-oversized-error",
    "H-be-invalid-200-limit",
    "H-be-error-bytes-below",
    "H-be-error-bytes-at",
}
AI_HTTP = {
    "H-existing-before-overload-same",
    "H-existing-before-overload-different",
    "H-existing-before-overload-uncomparable",
    "H-new-overloaded",
    "H-registration-race-same",
    "H-registration-race-different",
    "H-overload-concurrent-commit",
    "H-slot-retained-timeout",
    "H-slot-retained-caller-cancel",
    "H-chunked-request-at",
    "H-chunked-request-above",
    "H-receipt-preserved-response-bytes",
    "H-receipt-preserved-response-suggestions",
    "H-receipt-preserved-response-changeText",
}


def raw(path="payloads/requests/base.json"):
    return (ROOT / path).read_bytes()


@lru_cache(maxsize=128)
def generated(name):
    return materialize(ROOT, GENERATED["recipes"], name)


def body(spec):
    return raw(spec["bodyFile"]) if "bodyFile" in spec else generated(spec["bodyRecipe"])


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


@pytest.mark.anyio
@pytest.mark.parametrize("case", STATIC, ids=lambda c: c["caseId"])
async def test_all_static_cases_over_http(case, tmp_path, caplog):
    provider = Provider(case["provider"])
    app, instance = build(
        tmp_path,
        provider=None if case["provider"]["mode"] == "DISABLED" else provider,
        timeout_seconds=0.02 if case["provider"]["mode"] == "TIMEOUT" else 20,
    )
    async with client(app) as http:
        result = await http.post(
            "/v2/analyses", content=body(case["request"]), headers=case["request"]["headers"]
        )
    expected = case["expected"]["ai"]
    assert_outcome(result, expected)
    assert provider.calls == expected["providerCalls"]
    assert row_count(instance) == (1 if provider.calls else 0)
    assert "synthetic-private-provider-error" not in result.text + caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("case", [c for c in LIMITS if "boundary" in c], ids=lambda c: c["caseId"])
async def test_all_limit_boundaries_over_http(case, tmp_path):
    provider = Provider(case["provider"])
    app, instance = build(tmp_path, provider=provider)
    async with client(app) as http:
        response = await http.post(
            "/v2/analyses", content=body(case["request"]), headers=case["request"]["headers"]
        )
    assert_outcome(response, case["expected"]["ai"])
    assert provider.calls == case["expected"]["ai"]["providerCalls"]
    assert row_count(instance) == int(case["expected"]["receiptCreatedByThisRequest"])


def test_http_coverage_accounts_for_every_case_without_claiming_be_execution():
    assert AI_HTTP.isdisjoint(BE_ONLY)
    assert AI_HTTP | BE_ONLY == HTTP.keys()
    assert len(AI_HTTP) == 14 and len(BE_ONLY) == 11
    assert len(STATIC) == 146
    assert len([c for c in LIMITS if "boundary" in c]) == 96


def seed(instance, payload, *, uncomparable=False):
    request = parse_request(payload)
    fingerprint = instance.receipts.digester.fingerprint(request)
    if uncomparable:
        fingerprint = Fingerprint(
            fingerprint.canonicalization_version, "unavailable-key", fingerprint.digest
        )
    assert instance.receipts.store.register(Receipt("v2", request.request_id, fingerprint)) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case_id",
    [
        "H-existing-before-overload-same",
        "H-existing-before-overload-different",
        "H-existing-before-overload-uncomparable",
        "H-new-overloaded",
        "H-overload-concurrent-commit",
    ],
)
async def test_existing_receipt_precedes_provider_capacity(case_id, tmp_path):
    case = HTTP[case_id]
    provider = Provider()
    app, instance = build(tmp_path, provider=provider)
    setup = case["setup"]
    if setup["existingReceipt"]:
        seed(
            instance,
            raw(setup["storedRequestBodyFile"]),
            uncomparable=setup["existingReceipt"] == "uncomparable",
        )
    occupied = [instance.slots.acquire() for _ in range(setup["occupiedSlots"])]
    async with client(app) as http:
        result = await http.post(
            "/v2/analyses", content=raw(setup["requestBodyFile"]), headers=headers()
        )
    assert_outcome(result, case["expected"]["ai"])
    assert provider.calls == 0
    assert row_count(instance) == int(bool(setup["existingReceipt"]))
    if case_id == "H-overload-concurrent-commit":
        seed(instance, raw(setup["competitorBodyFile"]))
        assert row_count(instance) == 1
    for lease in occupied:
        lease.release()


@pytest.mark.anyio
@pytest.mark.parametrize("case_id", ["H-registration-race-same", "H-registration-race-different"])
async def test_atomic_registration_rechecks_after_slot_acquisition(case_id, tmp_path, monkeypatch):
    case = HTTP[case_id]
    provider = Provider()
    app, instance = build(tmp_path, provider=provider)
    original_register = instance.receipts.store.register
    competitor = parse_request(raw(case["setup"]["competitorBodyFile"]))
    receipt = Receipt(
        "v2", competitor.request_id, instance.receipts.digester.fingerprint(competitor)
    )

    def competing_register(own_receipt):
        assert instance.slots.active == 1
        assert original_register(receipt) is None
        return original_register(own_receipt)

    monkeypatch.setattr(instance.receipts.store, "register", competing_register)
    async with client(app) as http:
        response = await http.post("/v2/analyses", content=raw(), headers=headers())
    assert_outcome(response, case["expected"]["ai"])
    assert provider.calls == 0 and instance.slots.active == 0
    assert instance.receipts.store.get("v2", competitor.request_id) == receipt


@pytest.mark.anyio
async def test_B_uncomparable_record_over_http(tmp_path):
    case = next(
        c for c in json.loads(raw("cases/receipts.json")) if c["caseId"] == "B-uncomparable-record"
    )
    provider = Provider()
    app, instance = build(tmp_path, provider=provider)
    seed(instance, raw(case["seedReceipt"]["requestBodyFile"]), uncomparable=True)
    step = case["steps"][-1]
    async with client(app) as http:
        response = await http.post(
            "/v2/analyses", content=body(step["request"]), headers=step["request"]["headers"]
        )
    assert_outcome(response, step["expected"]["ai"])
    assert row_count(instance) == 1 and provider.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("case_id", ["H-chunked-request-at", "H-chunked-request-above"])
async def test_request_without_length_header(case_id, tmp_path):
    case = HTTP[case_id]
    setup = case["setup"]
    boundary = next(c for c in LIMITS if c["caseId"] == setup["boundaryCaseId"])
    provider = Provider(boundary["provider"])
    app, instance = build(tmp_path, provider=provider)
    payload = generated(setup["bodyRecipe"])

    async def chunks():
        for offset in range(0, len(payload), setup["chunkBytes"]):
            yield payload[offset : offset + setup["chunkBytes"]]

    async with client(app) as http:
        response = await http.post("/v2/analyses", content=chunks(), headers=headers(payload))
        assert "content-length" not in response.request.headers
    assert_outcome(response, boundary["expected"]["ai"])
    assert provider.calls == int(case["expected"]["accepted"])
    assert row_count(instance) == provider.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case_id",
    [
        "H-receipt-preserved-response-bytes",
        "H-receipt-preserved-response-suggestions",
        "H-receipt-preserved-response-changeText",
    ],
)
async def test_oversized_result_preserves_receipt_and_never_retries(case_id, tmp_path):
    case = HTTP[case_id]
    provider = Provider({"mode": "RETURN", "bodyRecipe": case["setup"]["providerBodyRecipe"]})
    app, instance = build(tmp_path, provider=provider)
    payload = generated(case["setup"]["requestBodyRecipe"])
    async with client(app) as http:
        for step in case["steps"]:
            before = provider.calls
            response = await http.post("/v2/analyses", content=payload, headers=headers(payload))
            assert_outcome(
                response, {"httpStatus": step["expectedHttpStatus"], "code": step["expectedCode"]}
            )
            assert provider.calls - before == step["providerCalls"]
            assert "suggestions" not in response.json()
    assert row_count(instance) == 1
    assert provider.calls == case["expected"]["totalProviderCalls"]


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


@pytest.mark.anyio
@pytest.mark.parametrize("case_id", ["H-slot-retained-timeout", "H-slot-retained-caller-cancel"])
async def test_provider_capacity_retained_until_actual_finish(case_id, tmp_path):
    case = HTTP[case_id]
    provider = StubbornProvider()
    app, instance = build(
        tmp_path, provider=provider, timeout_seconds=0.03 if case_id.endswith("timeout") else 20
    )
    other_lease = instance.slots.acquire()
    async with client(app) as http:
        caller = asyncio.create_task(http.post("/v2/analyses", content=raw(), headers=headers()))
        await asyncio.wait_for(provider.started.wait(), 2)
        if case_id.endswith("caller-cancel"):
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        else:
            response = await caller
            assert_outcome(
                response,
                {
                    "httpStatus": case["expected"]["targetCallerHttpStatus"],
                    "code": case["expected"]["targetCallerCode"],
                },
            )
        await asyncio.wait_for(provider.cancelled.wait(), 2)
        assert instance.slots.active == 2
        assert row_count(instance) == 1
        retry = raw(case["setup"]["otherRequestBodyFile"])
        overloaded = await http.post("/v2/analyses", content=retry, headers=headers(retry))
        assert_outcome(overloaded, {"httpStatus": 503, "code": "AI_OVERLOADED"})
        assert provider.calls == 1 and row_count(instance) == 1
        tasks = set(instance.tasks)
        provider.release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert instance.slots.active == 1
        other_lease.release()
        assert app.state.v2_runtime.admissions.active == 0
