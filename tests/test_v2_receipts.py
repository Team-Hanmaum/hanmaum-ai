"""Run all shared receipt scenarios against SQLite and the internal v2 service.

HTTP headers/status transport and expected.be remain the next integration layers.
"""

import asyncio
import hashlib
import json
import multiprocessing
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretBytes

from app.errors import AnalysisFailure
from app.v2.comparison import (
    CANONICALIZATION_VERSION,
    ComparisonUnavailable,
    Fingerprint,
    RequestDigester,
    canonicalize_v2,
)
from app.v2.receipts import Receipt, ReceiptGate, SQLiteReceiptStore
from app.v2.services import AnalysisService
from app.v2.validation import parse_request

ROOT = Path(__file__).parent / "fixtures/v2"
CASES = json.loads((ROOT / "cases/receipts.json").read_bytes())
KEY = b"synthetic-receipt-test-key-only-001"
RULE = "fixture-rule-1"
KEY_VERSION = "fixture-key-1"


def raw(relative="payloads/requests/base.json"):
    return (ROOT / relative).read_bytes()


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
        result = await instance.analyze(payload)
        outcome = {"httpStatus": 200, "body": result.model_dump(mode="json")}
    except AnalysisFailure as exc:
        assert exc.message.strip()
        outcome = {"httpStatus": exc.status_code, "code": exc.code}
    except ComparisonUnavailable as exc:
        assert not hasattr(exc, "status_code") and not hasattr(exc, "code")
        outcome = {"outcome": "BLOCKED"}
    return outcome | {"providerCalls": provider.calls - before}


def expected_result(expected):
    result = {k: v for k, v in expected.items() if k not in {"bodyFile", "messagePolicy"}}
    if "bodyFile" in expected:
        result["body"] = json.loads(raw(expected["bodyFile"]))
    return result


class RacingStore(SQLiteReceiptStore):
    """Force both instances past the initial absent read before either registers."""

    def __init__(self, path, barrier):
        super().__init__(path, lock_timeout_seconds=2)
        self.barrier = barrier

    def register(self, receipt):
        self.barrier.wait(timeout=10)
        return super().register(receipt)


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["caseId"])
async def test_shared_receipt_scenario(case, tmp_path, monkeypatch):
    path = tmp_path / "receipts.sqlite3"
    store = store_at(path, initialize=True)
    comparison = digester()
    keys = {KEY_VERSION: SecretBytes(KEY)}
    rules = {RULE: canonicalize_v2}
    active_key, active_rule = KEY_VERSION, RULE
    provider = FakeProvider()
    instance = service(store, provider, comparison)
    total_calls = 0

    if seed := case.get("seedReceipt"):
        assert seed["executionState"] in {"SUCCEEDED", "FAILED", "IN_PROGRESS"}
        # State is a fixture precondition, deliberately not a production schema field.
        if seed["contractVersion"] == "v2":
            request = parse_request(raw(seed["requestBodyFile"]))
            request_id, fingerprint = request.request_id, comparison.fingerprint(request)
        else:
            request_id = UUID(json.loads(raw(seed["requestBodyFile"]))["requestId"])
            fingerprint = Fingerprint(RULE, KEY_VERSION, hashlib.sha256(b"v1-seed").digest())
        assert store.register(Receipt(seed["contractVersion"], request_id, fingerprint)) is None

    for step in case["steps"]:
        action = step["action"]
        if action == "SUBMIT":
            provider.config = step["provider"]
            result = await submit(instance, provider, raw(step["request"]["bodyFile"]))
            assert result == expected_result(step["expected"]["ai"])
            total_calls += result["providerCalls"]
        elif action == "SUBMIT_CONCURRENT":
            barrier = threading.Barrier(step["instanceCount"])
            providers = [FakeProvider(step["provider"]) for _ in step["requests"]]
            instances = [service(RacingStore(path, barrier), p) for p in providers]
            results = await asyncio.gather(
                *[
                    submit(s, p, raw(r["bodyFile"]))
                    for s, p, r in zip(instances, providers, step["requests"], strict=True)
                ]
            )
            assert Counter(json.dumps(r, sort_keys=True) for r in results) == Counter(
                json.dumps(expected_result(e), sort_keys=True)
                for e in step["expected"]["aiResults"]
            )
            total_calls += sum(r["providerCalls"] for r in results)
        elif action == "RESTART_AI_PRESERVE_RECEIPTS":
            store = store_at(path)
            instance = service(store, provider, comparison)
        elif action == "FAIL_RECEIPT_STORE":
            if step["operation"] == "READ":

                def fail_read(*args):
                    raise sqlite3.OperationalError("sensitive-storage-detail")

                monkeypatch.setattr(store, "_read", fail_read)
            else:
                assert step["operation"] == "REGISTER"
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute(
                        "CREATE TRIGGER deny_insert BEFORE INSERT ON receipts "
                        "BEGIN SELECT RAISE(ABORT, 'sensitive-storage-detail'); END"
                    )
        elif action == "ROTATE_COMPARISON_METADATA":
            if step["field"] == "hmacKeyVersion":
                if not step["oldComparisonAvailable"]:
                    keys.clear()
                active_key = step["newVersion"]
                keys[active_key] = SecretBytes(b"different-" + KEY)
            else:
                assert step["field"] == "canonicalizationVersion"
                if not step["oldComparisonAvailable"]:
                    rules.clear()
                active_rule = step["newVersion"]
                # Deliberately different bytes catch accidental use of the new rule for old rows.
                rules[active_rule] = lambda request: b"fixture-rule-2:" + canonicalize_v2(request)
            comparison = digester(
                keys=keys,
                active_key_version=active_key,
                rules=rules,
                active_rule_version=active_rule,
            )
            instance = service(store, provider, comparison)
        else:
            pytest.fail(f"Unimplemented fixture action: {action}")

    assert total_calls == case["expected"]["totalProviderCalls"]
    if "receiptCount" in case["expected"]:
        assert len(rows(path)) == case["expected"]["receiptCount"]
    if "bodyEquality" in case:
        left, right = [parse_request(raw(s["request"]["bodyFile"])) for s in case["steps"]]
        assert (canonicalize_v2(left) == canonicalize_v2(right)) == case["bodyEquality"]


def test_receipt_fixture_coverage():
    manifest = json.loads(raw("manifest.json"))
    assert manifest["revision"] == "v2-20261016.2"
    assert len(CASES) == len({c["caseId"] for c in CASES}) == 30
    assert {c["contractVersion"] for c in CASES} == {"v2"}


def test_canonical_format_and_original_snapshot():
    request = parse_request(raw())
    before = request.model_dump(mode="json")
    result = canonicalize_v2(request)
    data = json.loads(result)
    assert data["recordedAt"].endswith(".123456Z")
    assert data["recordedAt"].startswith("2026-10-16T00:00:00")
    assert data["content"] == before["content"]
    assert data["candidates"] == before["candidates"]
    assert (
        result
        == json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )
    assert request.model_dump(mode="json") == before


@pytest.mark.parametrize("changed", ["rule", "key", "both"])
def test_old_metadata_is_used_even_after_rotation(changed, tmp_path):
    path = tmp_path / "receipts.sqlite3"
    store = store_at(path, initialize=True)
    request = parse_request(raw())
    ReceiptGate(store, digester()).claim(request)
    original = rows(path)
    rules = {RULE: canonicalize_v2, "new-rule": lambda r: b"changed:" + canonicalize_v2(r)}
    keys = {KEY_VERSION: SecretBytes(KEY), "new-key": SecretBytes(b"new-" + KEY)}
    updated = digester(
        keys=keys,
        rules=rules,
        active_rule_version="new-rule" if changed in {"rule", "both"} else RULE,
        active_key_version="new-key" if changed in {"key", "both"} else KEY_VERSION,
    )
    with pytest.raises(AnalysisFailure, match="REQUEST_ALREADY_RECEIVED"):
        ReceiptGate(store, updated).claim(request)
    modified = json.loads(raw())
    modified["content"] += " changed"
    with pytest.raises(AnalysisFailure, match="REQUEST_ID_REUSED"):
        ReceiptGate(store, updated).claim(parse_request(json.dumps(modified)))
    assert rows(path) == original


@pytest.mark.parametrize("missing", ["key", "rule"])
def test_unavailable_historic_metadata_blocks_without_overwrite(missing, tmp_path):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    request = parse_request(raw())
    ReceiptGate(store, digester()).claim(request)
    original = rows(store.path)
    updated = digester(
        keys={"new-key": SecretBytes(b"new-" + KEY)}
        if missing == "key"
        else {KEY_VERSION: SecretBytes(KEY)},
        active_key_version="new-key" if missing == "key" else KEY_VERSION,
        rules={"new-rule": canonicalize_v2} if missing == "rule" else {RULE: canonicalize_v2},
        active_rule_version="new-rule" if missing == "rule" else RULE,
    )
    with pytest.raises(ComparisonUnavailable):
        ReceiptGate(store, updated).claim(request)
    assert rows(store.path) == original


def test_key_material_and_configuration_are_not_exposed_or_mutated():
    keys = {KEY_VERSION: SecretBytes(KEY)}
    rules = {RULE: canonicalize_v2}
    comparison = digester(keys=keys, rules=rules)
    request = parse_request(raw())
    expected = comparison.fingerprint(request)
    keys.clear()
    rules.clear()
    assert comparison.fingerprint(request) == expected
    assert KEY.decode() not in repr(comparison)
    assert expected.digest.hex() not in repr(expected)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode,code",
    [
        ("UNAVAILABLE", "AI_UNAVAILABLE"),
        ("TIMEOUT", "AI_TIMEOUT"),
        ("INVALID", "INVALID_AI_RESPONSE"),
    ],
)
async def test_provider_failure_keeps_receipt_and_blocks_retry(tmp_path, mode, code, caplog):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    provider = FakeProvider({"mode": mode, "bodyFile": "payloads/requests/base.json"})
    instance = service(store, provider, timeout_seconds=0.01)
    result = await submit(instance, provider, raw())
    assert result["code"] == code and result["providerCalls"] == 1
    before = rows(store.path)
    second = await submit(service(store_at(store.path), provider), provider, raw())
    assert second == {"httpStatus": 409, "code": "REQUEST_ALREADY_RECEIVED", "providerCalls": 0}
    assert rows(store.path) == before
    assert "sensitive-provider-detail" not in caplog.text


@pytest.mark.anyio
async def test_provider_mutable_copy_does_not_change_validation_basis(tmp_path):
    class MutatingProvider:
        async def analyze(self, request):
            copied = request.model_dump(mode="json")
            copied["content"] = "invented-provider-evidence"
            response = json.loads(raw("payloads/responses/update.json"))
            response["suggestions"][0]["evidence"] = copied["content"]
            return response

    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    with pytest.raises(AnalysisFailure, match="INVALID_AI_RESPONSE"):
        await service(store, MutatingProvider()).analyze(raw())
    assert len(rows(store.path)) == 1


@pytest.mark.anyio
async def test_cancelled_provider_keeps_receipt_while_in_progress(tmp_path):
    started = asyncio.Event()

    class WaitingProvider:
        calls = 0

        async def analyze(self, request):
            self.calls += 1
            started.set()
            await asyncio.Event().wait()

    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    provider = WaitingProvider()
    instance = service(store, provider)
    task = asyncio.create_task(instance.analyze(raw()))
    try:
        await asyncio.wait_for(started.wait(), 5)
        result = await submit(service(store_at(store.path), provider), provider, raw())
        assert result["code"] == "REQUEST_ALREADY_RECEIVED"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (await submit(instance, provider, raw()))["code"] == "REQUEST_ALREADY_RECEIVED"
    assert provider.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "relative",
    [
        "payloads/requests/invalid-fraction-seven.json",
        "payloads/requests/invalid-fraction-seven-zero.json",
    ],
)
async def test_invalid_request_does_not_touch_store(tmp_path, relative):
    provider = FakeProvider()
    store = store_at(tmp_path / "does-not-exist.sqlite3")
    assert (await submit(service(store, provider), provider, raw(relative)))[
        "code"
    ] == "INVALID_REQUEST"
    assert provider.calls == 0 and not store.path.exists()


@pytest.mark.anyio
@pytest.mark.parametrize("damage", ["missing", "deleted", "empty", "schema", "corrupt", "locked"])
async def test_storage_failure_is_safe_and_never_recreates_database(tmp_path, damage, caplog):
    path = tmp_path / "receipts.sqlite3"
    store = store_at(
        path, initialize=damage not in {"missing", "empty", "corrupt"}, lock_timeout_seconds=0
    )
    lock = None
    if damage == "deleted":
        path.unlink()
    elif damage == "empty":
        path.touch()
    elif damage == "schema":
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("DROP TABLE receipts")
    elif damage == "corrupt":
        path.write_bytes(b"sensitive-corrupt-db-content")
    elif damage == "locked":
        lock = sqlite3.connect(path)
        lock.execute("BEGIN EXCLUSIVE")
    provider = FakeProvider()
    try:
        result = await submit(service(store, provider), provider, raw())
        assert result == {
            "httpStatus": 503,
            "code": "RECEIPT_STORE_UNAVAILABLE",
            "providerCalls": 0,
        }
        if damage in {"missing", "deleted"}:
            assert not path.exists()
        if damage == "empty":
            assert path.stat().st_size == 0
        assert "sensitive" not in caplog.text
    finally:
        if lock:
            lock.close()


@pytest.mark.anyio
async def test_registered_data_contains_only_identity_metadata_and_digest(tmp_path):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    request = parse_request(raw())
    provider = FakeProvider()
    result = await service(store, provider).analyze(raw())
    stored = rows(store.path)
    assert len(stored) == 1 and len(stored[0]) == 5
    assert stored[0][:4] == ("v2", str(request.request_id), RULE, KEY_VERSION)
    assert len(stored[0][4]) == 32
    for file in tmp_path.iterdir():
        content = file.read_bytes()
        assert KEY not in content
        assert request.content.encode() not in content
        assert result.suggestions[0].title.encode() not in content
        assert canonicalize_v2(request) not in content


def process_submit(path, barrier, result_queue, request_raw):
    """Spawn-safe target: no shared Python gate, connection, event loop or provider."""
    provider = FakeProvider()
    store = RacingStore(Path(path), barrier) if barrier else store_at(Path(path))
    result = asyncio.run(submit(service(store, provider), provider, request_raw))
    result_queue.put(result)


def run_processes(path, payloads, *, concurrent):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(len(payloads)) if concurrent else None
    result_queue = context.Queue()
    processes = [
        context.Process(target=process_submit, args=(str(path), barrier, result_queue, p))
        for p in payloads
    ]
    try:
        for process in processes:
            process.start()
        results = [result_queue.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        return results
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
            process.close()
        result_queue.close()
        result_queue.join_thread()


@pytest.mark.parametrize("different_body", [False, True])
def test_separate_processes_race_and_process_restart(tmp_path, different_body):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    other = raw("payloads/requests/compare-content-space.json") if different_body else raw()
    results = run_processes(store.path, [raw(), other], concurrent=True)
    assert sum(r["providerCalls"] for r in results) == 1
    duplicate_code = "REQUEST_ID_REUSED" if different_body else "REQUEST_ALREADY_RECEIVED"
    assert Counter((r["httpStatus"], r.get("code")) for r in results) == Counter(
        [(200, None), (409, duplicate_code)]
    )
    assert len(rows(store.path)) == 1
    again = run_processes(store.path, [raw()], concurrent=False)[0]
    assert again["httpStatus"] == 409 and again["providerCalls"] == 0


def test_independent_request_ids_are_not_deduplicated(tmp_path):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    request = json.loads(raw())
    ReceiptGate(store, digester()).claim(parse_request(json.dumps(request)))
    request["requestId"] = str(uuid4())
    ReceiptGate(store, digester()).claim(parse_request(json.dumps(request)))
    assert len(rows(store.path)) == 2


def test_explicit_initialization_preserves_existing_receipts(tmp_path):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    ReceiptGate(store, digester()).claim(parse_request(raw()))
    before = rows(store.path)
    store.initialize()
    assert rows(store.path) == before


def test_default_rule_version_is_explicit():
    comparison = RequestDigester(keys={"key-1": SecretBytes(KEY)}, active_key_version="key-1")
    assert (
        comparison.fingerprint(parse_request(raw())).canonicalization_version
        == CANONICALIZATION_VERSION
    )


@pytest.mark.anyio
@pytest.mark.parametrize("committed", [False, True])
async def test_uncertain_commit_never_calls_provider(tmp_path, committed):
    path = tmp_path / "receipts.sqlite3"
    store_at(path, initialize=True)

    class FaultyCommit:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            if exc_type is not None:
                return self.connection.__exit__(exc_type, exc, traceback)
            if committed:
                self.connection.commit()
            else:
                self.connection.rollback()
            raise sqlite3.OperationalError("sensitive-commit-error")

    class FaultyStore(SQLiteReceiptStore):
        def _connect(self, **kwargs):
            return FaultyCommit(super()._connect(**kwargs))

    provider = FakeProvider()
    faulty = FaultyStore(path, lock_timeout_seconds=2)
    result = await submit(service(faulty, provider), provider, raw())
    assert result == {"httpStatus": 503, "code": "RECEIPT_STORE_UNAVAILABLE", "providerCalls": 0}
    assert len(rows(path)) == int(committed)
    # Do not retry automatically. A later independently submitted request consults disk.
    result = await submit(service(store_at(path), provider), provider, raw())
    assert result["httpStatus"] == (409 if committed else 200)
    assert result["providerCalls"] == (0 if committed else 1)


@pytest.mark.anyio
async def test_cancelled_registration_worker_may_commit_but_never_call_provider(tmp_path):
    path = tmp_path / "receipts.sqlite3"
    store_at(path, initialize=True)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class PausingStore(SQLiteReceiptStore):
        def register(self, receipt):
            entered.set()
            try:
                assert release.wait(timeout=10)
                return super().register(receipt)
            finally:
                finished.set()

    provider = FakeProvider()
    instance = service(PausingStore(path, lock_timeout_seconds=2), provider)
    task = asyncio.create_task(instance.analyze(raw()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    assert provider.calls == 0 and len(rows(path)) == 1
    assert (await submit(service(store_at(path), provider), provider, raw()))[
        "code"
    ] == "REQUEST_ALREADY_RECEIVED"
    assert provider.calls == 0


@pytest.mark.anyio
async def test_database_disappearing_between_read_and_register_is_not_recreated(tmp_path):
    path = tmp_path / "receipts.sqlite3"
    store_at(path, initialize=True)

    class DisappearingStore(SQLiteReceiptStore):
        def get(self, contract_version, request_id):
            assert super().get(contract_version, request_id) is None
            self.path.unlink()
            return None

    provider = FakeProvider()
    instance = service(DisappearingStore(path, lock_timeout_seconds=2), provider)
    assert (await submit(instance, provider, raw()))["code"] == "RECEIPT_STORE_UNAVAILABLE"
    assert provider.calls == 0 and not path.exists()


@pytest.mark.anyio
async def test_provider_errors_cannot_supply_arbitrary_public_error_details(tmp_path, caplog):
    class UnsafeProvider:
        async def analyze(self, request):
            raise AnalysisFailure(418, request.content, KEY.decode())

    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    with pytest.raises(AnalysisFailure) as caught:
        await service(store, UnsafeProvider()).analyze(raw())
    assert caught.value.code == "AI_UNAVAILABLE" and caught.value.status_code == 502
    assert KEY.decode() not in str(caught.value) + caught.value.message + caplog.text


@pytest.mark.anyio
async def test_disabled_provider_does_not_require_or_create_receipt_store(tmp_path):
    store = store_at(tmp_path / "missing.sqlite3")
    with pytest.raises(AnalysisFailure, match="AI_NOT_CONFIGURED"):
        await service(store, None).analyze(raw())
    assert not store.path.exists()


@pytest.mark.parametrize("damage", ["unknown-rule", "unknown-key", "bad-digest"])
def test_damaged_comparison_metadata_blocks_calls(tmp_path, damage):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    request = parse_request(raw())
    ReceiptGate(store, digester()).claim(request)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        if damage == "unknown-rule":
            connection.execute("UPDATE receipts SET canonicalization_version = 'unknown'")
        elif damage == "unknown-key":
            connection.execute("UPDATE receipts SET hmac_key_version = 'unknown'")
        else:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE receipts SET digest = x'00'")
    before = rows(store.path)
    with pytest.raises(ComparisonUnavailable):
        ReceiptGate(store, digester()).claim(request)
    assert rows(store.path) == before


@pytest.mark.anyio
async def test_duplicate_json_keys_are_rejected_before_registration(tmp_path):
    store = store_at(tmp_path / "missing.sqlite3")
    provider = FakeProvider()
    duplicate = raw().replace(b'"content":', b'"content": "first", "content":', 1)
    result = await submit(service(store, provider), provider, duplicate)
    assert result["code"] == "INVALID_REQUEST" and provider.calls == 0
    assert not store.path.exists()


@pytest.mark.parametrize("version", [0, 999])
def test_unsupported_database_schema_is_not_reinitialized(tmp_path, version):
    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    ReceiptGate(store, digester()).claim(parse_request(raw()))
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute(f"PRAGMA user_version = {version}")
    before = rows(store.path)
    with pytest.raises(AnalysisFailure, match="RECEIPT_STORE_UNAVAILABLE"):
        store.initialize()
    assert rows(store.path) == before


@pytest.mark.parametrize("bad_value", [-1, float("nan"), float("inf")])
def test_lock_timeout_must_be_explicit_finite_and_nonnegative(tmp_path, bad_value):
    with pytest.raises(ValueError):
        SQLiteReceiptStore(tmp_path / "receipts.sqlite3", lock_timeout_seconds=bad_value)


def test_missing_active_key_or_empty_secret_cannot_authorize_calls():
    with pytest.raises(ValueError):
        digester(keys={})
    with pytest.raises(ValueError):
        digester(keys={KEY_VERSION: SecretBytes(b"")})
    with pytest.raises(ValueError):
        digester(keys={KEY_VERSION: SecretBytes(b"x" * 31)})


@pytest.mark.parametrize(
    "rule,key,digest",
    [
        (None, KEY_VERSION, b"x" * 32),
        (RULE, None, b"x" * 32),
        ("", KEY_VERSION, b"x" * 32),
        (RULE, KEY_VERSION, "not-bytes"),
    ],
)
def test_invalid_stored_metadata_cannot_fall_back_to_active_versions(rule, key, digest):
    with pytest.raises(ComparisonUnavailable):
        digester().matches(parse_request(raw()), Fingerprint(rule, key, digest))


@pytest.mark.anyio
async def test_failing_canonicalizer_blocks_without_exposing_input(tmp_path, caplog):
    def broken_rule(request):
        raise ValueError(request.content + KEY.decode())

    store = store_at(tmp_path / "receipts.sqlite3", initialize=True)
    provider = FakeProvider()
    instance = service(store, provider, digester(rules={RULE: broken_rule}))
    with pytest.raises(ComparisonUnavailable) as caught:
        await instance.analyze(raw())
    assert KEY.decode() not in str(caught.value) + caplog.text
    assert provider.calls == 0 and rows(store.path) == []
