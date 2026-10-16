"""Regression cases for receipt boundary, cancellation and schema review findings."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime

import pytest
from test_v2_receipts import (
    KEY_VERSION,
    RULE,
    FakeProvider,
    digester,
    raw,
    rows,
    service,
    store_at,
    submit,
)

from app.errors import AnalysisFailure
from app.v2.comparison import ComparisonUnavailable, Fingerprint, canonicalize_v2
from app.v2.diagnostics import FailureEvent, failure_counts, record_failure
from app.v2.receipts import SCHEMA, SCHEMA_VERSION, Receipt
from app.v2.services import _abandoned_tasks, _provider_tasks
from app.v2.validation import parse_request

BOUNDARIES = [
    ("0001-01-01T00:00:00+01:00", "0001-01-01T01:00:00+02:00", "0000-12-31T23:00:00.000000Z"),
    ("9999-12-31T23:00:00-02:00", "9999-12-31T22:00:00-03:00", "10000-01-01T01:00:00.000000Z"),
    (
        "0001-01-01T00:01:00.123456+23:59",
        "0001-01-01T00:00:00.123456+23:58",
        "0000-12-31T00:02:00.123456Z",
    ),
    (
        "9999-12-31T23:58:59.999999-23:59",
        "9999-12-31T23:59:59.999999-23:58",
        "10000-01-01T23:57:59.999999Z",
    ),
]


def request_at(timestamp):
    data = json.loads(raw())
    data["recordedAt"] = timestamp
    return json.dumps(data).encode()


@pytest.mark.anyio
@pytest.mark.parametrize("first,second,canonical", BOUNDARIES)
async def test_valid_boundary_timestamp_is_registered_and_deduplicated(
    tmp_path, first, second, canonical
):
    request = parse_request(request_at(first))
    assert json.loads(canonicalize_v2(request))["recordedAt"] == canonical
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    provider = FakeProvider()
    instance = service(store, provider)
    assert (await submit(instance, provider, request_at(first)))["httpStatus"] == 200
    assert (await submit(instance, provider, request_at(second)))[
        "code"
    ] == "REQUEST_ALREADY_RECEIVED"
    assert provider.calls == len(rows(store.path)) == 1
    assert request.recorded_at == first


@pytest.mark.anyio
async def test_provider_swallowing_timeout_cannot_return_success(tmp_path):
    class SuppressingProvider:
        calls = 0

        async def analyze(self, request):
            self.calls += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return raw("payloads/responses/update.json")

    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    provider = SuppressingProvider()
    instance = service(store, provider, timeout_seconds=0.01)
    result = await submit(instance, provider, raw())
    assert result == {"httpStatus": 504, "code": "AI_TIMEOUT", "providerCalls": 1}
    assert (await submit(instance, provider, raw()))["code"] == "REQUEST_ALREADY_RECEIVED"
    assert provider.calls == 1


@pytest.mark.parametrize(
    "constraint",
    [
        "",
        " CHECK(length(digest) = 32)",
        " CHECK(typeof(digest) = 'blob')",
        " CHECK(typeof(digest) = 'blob' AND length(digest) = 32 OR 1)",
        " /* CHECK(typeof(digest) = 'blob' AND length(digest) = 32) */",
    ],
)
def test_matching_columns_without_required_digest_constraint_are_not_accepted(tmp_path, constraint):
    store = store_at(tmp_path / "receipts.sqlite3")
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute(
            SCHEMA.replace(" CHECK(typeof(digest) = 'blob' AND length(digest) = 32)", constraint)
        )
        connection.execute("PRAGMA user_version = 1")
    request = parse_request(raw())
    with pytest.raises(AnalysisFailure, match="RECEIPT_STORE_UNAVAILABLE"):
        store.register(Receipt("v2", request.request_id, digester().fingerprint(request)))
    assert rows(store.path) == []


@pytest.mark.parametrize(
    "fingerprint",
    [
        Fingerprint(RULE, KEY_VERSION, b"short"),
        Fingerprint(RULE, KEY_VERSION, b"x" * 33),
        Fingerprint(RULE, KEY_VERSION, "x" * 32),
        Fingerprint(RULE, KEY_VERSION, bytearray(32)),
        Fingerprint("", KEY_VERSION, b"x" * 32),
        Fingerprint(RULE, None, b"x" * 32),
    ],
)
def test_invalid_fingerprint_is_rejected_before_database_access(tmp_path, monkeypatch, fingerprint):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)

    def unexpected_connect(*args, **kwargs):
        pytest.fail("Invalid fingerprint reached the database")

    monkeypatch.setattr(store, "_connect", unexpected_connect)
    receipt = Receipt("v2", parse_request(raw()).request_id, fingerprint)
    with pytest.raises(AnalysisFailure, match="RECEIPT_STORE_UNAVAILABLE"):
        store.register(receipt)


@pytest.mark.parametrize(
    "timestamp",
    [
        "0001-01-01T00:00:00Z",
        "0001-01-02T00:00:00+23:59",
        "0004-02-29T00:00:00+09:00",
        "1900-03-01T00:00:00+09:00",
        "2000-03-01T00:00:00+09:00",
        "2026-10-17T09:10:11.123456+09:00",
        "2026-10-17T00:10:11.123456-00:00",
        "9999-12-30T23:59:59.999999-23:59",
        "9999-12-31T23:59:59.999999Z",
    ],
)
def test_previously_supported_timestamps_keep_identical_canonical_bytes(timestamp):
    request = parse_request(request_at(timestamp))
    legacy_body = request.model_dump(mode="json", by_alias=True)
    legacy_body["recordedAt"] = (
        datetime.fromisoformat(timestamp)
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    expected = json.dumps(
        legacy_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    assert canonicalize_v2(request) == expected


@pytest.mark.parametrize("first,second,canonical", BOUNDARIES)
def test_boundary_instants_do_not_collapse_subsecond_differences(first, second, canonical):
    original = parse_request(request_at(first))
    timestamp = datetime.fromisoformat(first)
    different = timestamp.replace(microsecond=timestamp.microsecond ^ 1).isoformat()
    assert canonicalize_v2(original) != canonicalize_v2(parse_request(request_at(different)))


@pytest.mark.anyio
@pytest.mark.parametrize("caller_cancelled", [False, True])
@pytest.mark.parametrize("late_error", [False, True])
async def test_wait_does_not_depend_on_provider_cancellation_cleanup(
    tmp_path, caplog, caller_cancelled, late_error
):
    entered, cancelled, release, finished = (asyncio.Event() for _ in range(4))
    loop = asyncio.get_running_loop()
    unhandled = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: unhandled.append(context))

    class StubbornProvider:
        calls = 0

        async def analyze(self, request):
            self.calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                if late_error:
                    raise RuntimeError("sensitive-late-provider-error") from None
                return raw("payloads/responses/update.json")
            finally:
                finished.set()

    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    provider = StubbornProvider()
    instance = service(store, provider, timeout_seconds=0.1)
    caller = asyncio.create_task(instance.analyze(raw()))
    try:
        async with asyncio.timeout(3):
            await entered.wait()
            if caller_cancelled:
                caller.cancel()
            await cancelled.wait()
            # No provider cleanup has completed, yet the caller must already be released.
            done, _ = await asyncio.wait({caller}, timeout=0.2)
            assert caller in done
            if caller_cancelled:
                with pytest.raises(asyncio.CancelledError):
                    await caller
            else:
                with pytest.raises(AnalysisFailure, match="AI_TIMEOUT"):
                    await caller
            assert not finished.is_set()
            assert len(_abandoned_tasks) == 1
            assert (await submit(instance, provider, raw()))["code"] == "REQUEST_ALREADY_RECEIVED"
            assert provider.calls == len(rows(store.path)) == 1
    finally:
        release.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        async with asyncio.timeout(3):
            await finished.wait()
        await asyncio.sleep(0)  # Run the provider's completion callback.
        loop.set_exception_handler(previous_handler)
    assert not _provider_tasks
    assert not _abandoned_tasks
    assert not unhandled
    assert "sensitive-late-provider-error" not in caplog.text
    events = [getattr(record, "failure_event", None) for record in caplog.records]
    assert (FailureEvent.PROVIDER_LATE_FAILURE in events) == late_error


@pytest.mark.anyio
async def test_immediate_adapter_exception_remains_sanitized(tmp_path, caplog):
    class BrokenAdapter:
        calls = 0

        def analyze(self, request):
            self.calls += 1
            raise RuntimeError("sensitive-adapter-error")

    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    provider = BrokenAdapter()
    result = await submit(service(store, provider), provider, raw())
    assert result == {"httpStatus": 502, "code": "AI_UNAVAILABLE", "providerCalls": 1}
    assert "sensitive-adapter-error" not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure,event,kind",
    [
        ("provider", FailureEvent.PROVIDER_FAILED, "other"),
        ("timeout", FailureEvent.PROVIDER_TIMEOUT, "rejected"),
        ("read", FailureEvent.RECEIPT_READ, "sqlite_busy"),
        ("register", FailureEvent.RECEIPT_REGISTER, "sqlite_constraint"),
        ("initialize", FailureEvent.RECEIPT_INITIALIZE, "os_error"),
        ("rule", FailureEvent.COMPARISON_FAILED, "value_error"),
        ("missing_rule", FailureEvent.COMPARISON_RULE_MISSING, "rejected"),
        ("missing_key", FailureEvent.COMPARISON_KEY_MISSING, "rejected"),
    ],
)
async def test_failure_paths_have_safe_diagnostics_and_counts(
    tmp_path, monkeypatch, caplog, failure, event, kind
):
    secret = "sensitive-body-key-path-provider-detail"
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    provider = FakeProvider()
    comparison = digester()
    request = parse_request(raw())
    if failure == "provider":

        async def fail_provider(request):
            raise RuntimeError(secret)

        monkeypatch.setattr(provider, "analyze", fail_provider)
    elif failure == "timeout":
        provider = FakeProvider({"mode": "TIMEOUT"})
    elif failure in {"read", "register", "initialize"}:

        def broken_connect(**kwargs):
            if failure == "initialize":
                raise OSError(secret)
            error = sqlite3.OperationalError(secret)
            error.sqlite_errorcode = (
                sqlite3.SQLITE_BUSY if failure == "read" else sqlite3.SQLITE_CONSTRAINT_CHECK
            )
            raise error

        monkeypatch.setattr(store, "_connect", broken_connect)
    elif failure == "rule":

        def broken_rule(request):
            raise ValueError(secret)

        comparison = digester(rules={RULE: broken_rule})

    before = failure_counts().get((event.value, kind), 0)
    with pytest.raises((AnalysisFailure, ComparisonUnavailable)) as caught:
        if failure == "register":
            store.register(Receipt("v2", request.request_id, comparison.fingerprint(request)))
        elif failure == "initialize":
            store.initialize()
        elif failure == "missing_rule":
            comparison.fingerprint(request, rule_version=secret)
        elif failure == "missing_key":
            comparison.fingerprint(request, key_version=secret)
        else:
            await service(store, provider, comparison=comparison, timeout_seconds=0.01).analyze(
                raw()
            )
    assert failure_counts()[(event.value, kind)] == before + 1
    records = [record for record in caplog.records if record.name == "app.v2.diagnostics"]
    assert [(record.failure_event, record.failure_kind) for record in records] == [
        (event.value, kind)
    ]
    assert all(record.exc_info is None and record.stack_info is None for record in records)
    assert secret not in str(caught.value) + str(vars(caught.value)) + repr(
        [vars(r) for r in records]
    )


def test_counter_is_thread_safe_and_diagnostics_use_only_fixed_labels(caplog):
    secret = "sensitive-custom-exception-class"
    error = type(secret, (sqlite3.Error,), {})(secret)
    error.sqlite_errorcode = secret
    key = (FailureEvent.RECEIPT_READ.value, "sqlite_other")
    before = failure_counts().get(key, 0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: record_failure(FailureEvent.RECEIPT_READ, error), range(64)))
    assert failure_counts()[key] == before + 64
    snapshot = failure_counts()
    snapshot[key] = -1
    assert failure_counts()[key] == before + 64
    assert secret not in repr([vars(record) for record in caplog.records])


def test_sql_values_remain_bound_and_schema_version_matches_literal(tmp_path):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    injected = "x'); DROP TABLE receipts; --"
    receipt = Receipt(
        injected, parse_request(raw()).request_id, Fingerprint(injected, injected, b"x" * 32)
    )
    assert store.register(receipt) is None
    assert store.get(injected, receipt.request_id) == receipt
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1
