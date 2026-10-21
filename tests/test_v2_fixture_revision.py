"""Validate new goldens and generated inputs, not HTTP admission/limit implementation."""

import hashlib
import json
from functools import lru_cache
from uuid import UUID

import pytest

from app.v2.schemas import AnalysisRequest, AnalysisResponse
from app.v2.validation import validate_references
from scripts.fixture_manifest import DEFAULT_ROOT, read_json
from scripts.fixture_payloads import materialize, measurements, string_units

ROOT = DEFAULT_ROOT
GENERATED = read_json(ROOT / "generated.json")
LIMITS = read_json(ROOT / "limits.json")["limits"]
CASES = read_json(ROOT / "cases/limits.json")
HTTP = read_json(ROOT / "cases/http.json")
HISTORY = read_json(ROOT / "revision-history.json")


@lru_cache(maxsize=128)
def payload(name):
    return materialize(ROOT, GENERATED["recipes"], name)


@pytest.mark.parametrize("name", GENERATED["payloads"])
def test_generated_bytes_match_frozen_lengths_and_hashes(name):
    entry = GENERATED["payloads"][name]
    raw = payload(name)
    assert measurements(raw, entry["side"]) == entry["expected"]

    # Count code points independently; Python len alone undercounts supplementary chars.
    def count(value):
        if isinstance(value, str):
            return sum(2 if ord(c) > 0xFFFF else 1 for c in value)
        if isinstance(value, dict):
            return sum(count(child) for child in value.values())
        if isinstance(value, list):
            return sum(count(child) for child in value)
        return 0

    assert count(json.loads(raw)) == entry["expected"]["stringUnits"]


@pytest.mark.parametrize("case", [c for c in CASES if "boundary" in c], ids=lambda c: c["caseId"])
def test_boundary_goldens_isolate_limits_and_disclose_coupled_contract_violations(case):
    request_raw = payload(case["request"]["bodyRecipe"])
    response_raw = payload(case["provider"]["bodyRecipe"])
    values = (
        measurements(request_raw, "request")["measurements"]
        | measurements(response_raw, "response")["measurements"]
    )
    boundary = case["boundary"]
    assert values[boundary["metric"]] == boundary["value"] == boundary["limit"] + boundary["delta"]
    assert boundary["limit"] == LIMITS[boundary["metric"]]
    assert sorted(k for k, value in values.items() if value > LIMITS[k]) == sorted(
        case["expectedLimitViolations"]
    )
    status = 200
    code = None
    if boundary["delta"] == 1:
        status, code = (
            (413, "REQUEST_TOO_LARGE")
            if boundary["metric"] == "request.bytes"
            else (422, "INVALID_REQUEST")
            if boundary["metric"].startswith("request.")
            else (502, "INVALID_AI_RESPONSE")
        )
    assert case["expected"]["ai"]["httpStatus"] == status
    assert case["expected"]["ai"].get("code") == code
    assert case["expected"]["ai"]["providerCalls"] == (0 if status in {413, 422} else 1)
    assert case["expected"]["receiptCreatedByThisRequest"] == (status not in {413, 422})
    # Inspect OTHER rules independently of resource limits. HTTP boundary outcomes
    # are executed against the unchanged goldens in test_v2_http.py.
    request = AnalysisRequest.model_validate(json.loads(request_raw))
    if boundary["coupledViolations"]:
        with pytest.raises(ValueError):
            response = AnalysisResponse.model_validate(json.loads(response_raw))
            validate_references(request, response)
        assert boundary["delta"] == 1
    else:
        response = AnalysisResponse.model_validate(json.loads(response_raw))
        validate_references(request, response)
        if case["expected"]["ai"]["httpStatus"] == 200:
            assert len(response.suggestions) == case["expected"]["be"]["storedProposalCount"]


def test_all_limits_have_three_boundaries_and_free_text_fields_are_covered():
    assert {
        (c["boundary"]["metric"], c["boundary"]["delta"]) for c in CASES if "boundary" in c
    } == {(metric, delta) for metric in LIMITS for delta in (-1, 0, 1)}
    ids = {c["caseId"] for c in CASES}
    for side in ("request", "response"):
        for field in read_json(ROOT / "limits.json")["freeTextFields"]:
            assert {f"L-{side}-free-{field}-{suffix}" for suffix in ("below", "at", "above")} <= ids


def test_decoded_counts_preserve_escapes_occurrences_and_preconversion_values():
    literal, escaped = payload("unicode-literal"), payload("unicode-escaped")
    assert literal != escaped and json.loads(literal) == json.loads(escaped)
    assert len(escaped) - len(literal) == 11
    assert measurements(literal, "request")["measurements"]["request.content"] == 3
    compact, canonical = payload("uuid-compact"), payload("request-minimal")
    assert UUID(json.loads(compact)["requestId"]) == UUID(json.loads(canonical)["requestId"])
    assert string_units(json.loads(canonical)) - string_units(json.loads(compact)) == 4
    assert string_units({"ignored-long-key": ["😀", "same", "same", None, True, 123]}) == 10
    opaque = json.loads(payload("opaque-direct-input"))["candidates"][0]["basis"][0]["text"]
    assert opaque == ' { "a": "same", "a": "same" }\n'
    assert json.loads(payload("unicode-not-normalized"))["content"] == " Cafe\u0301\n "


def test_recipe_graph_and_scenario_references_resolve():
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.endswith("Recipe"):
                    assert child in GENERATED["recipes"]
                if key.endswith("File"):
                    assert (ROOT / child).is_file()
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    list(walk([GENERATED, CASES, HTTP]))
    ids = {c["caseId"] for c in CASES}
    for case in HTTP:
        if "boundaryCaseId" in case["setup"]:
            assert case["setup"]["boundaryCaseId"] in ids
        assert case["executionCoverage"] == "SCENARIO_ONLY_RUNTIME_DEFERRED"
        assert case["expected"]["automaticRetries"] == case["expected"]["v1FallbackCalls"] == 0


def test_previous_manifest_and_existing_payloads_are_preserved():
    previous = HISTORY["previous"]
    old_bytes = (ROOT / previous["manifestFile"]).read_bytes()
    assert (
        hashlib.sha256(old_bytes).hexdigest()
        == previous["manifestSha256"]
        == "a65c27c8a8c323f3bdfb6fec0bddefe8cf251870f940e2410a2043b0dab83ff9"
    )
    old = json.loads(old_bytes)
    assert old["revision"] == "v2-20261016.2"
    for entry in old["files"]:
        if entry["path"] in {"README.md", "error-mapping.json", "cases/receipts.json"}:
            continue
        assert hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    manifest = read_json(ROOT / "manifest.json")
    assert set(manifest["caseIds"]) == set(old["caseIds"]) | set(HISTORY["addedCaseIds"])
    assert HISTORY["removedCaseIds"] == []
    assert HISTORY["changedCaseIds"] == ["B-uncomparable-record"]
    assert manifest["revision"] == GENERATED["revision"] == HISTORY["revision"] == "v2-20261021.1"
    receipts = read_json(ROOT / "cases/receipts.json")
    original = read_json(ROOT / "history/B-uncomparable-record.v2-20261016.2.json")
    restored = [original if c["caseId"] == original["caseId"] else c for c in receipts]
    restored_bytes = (json.dumps(restored, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    old_hash = next(e["sha256"] for e in old["files"] if e["path"] == "cases/receipts.json")
    assert hashlib.sha256(restored_bytes).hexdigest() == old_hash


def test_overload_priority_and_retention_scenarios_are_explicit():
    by_id = {c["caseId"]: c for c in HTTP}
    for variant, status, code in (
        ("same", 409, "REQUEST_ALREADY_RECEIVED"),
        ("different", 409, "REQUEST_ID_REUSED"),
        ("uncomparable", 503, "REQUEST_COMPARISON_UNAVAILABLE"),
    ):
        case = by_id[f"H-existing-before-overload-{variant}"]
        assert case["setup"]["occupiedSlots"] == case["setup"]["providerSlots"] == 2
        assert case["expected"]["ai"] == {"httpStatus": status, "code": code, "providerCalls": 0}
        assert case["expected"]["receiptCreatedByThisRequest"] is False
    for kind in ("timeout", "caller-cancel"):
        steps = by_id[f"H-slot-retained-{kind}"]["steps"]
        assert steps[1] == {"action": "ASSERT_OCCUPIED_SLOTS", "count": 2}
        assert steps[3]["action"] == "RELEASE_PROVIDER_AND_WAIT_FINISH"
        assert steps[4] == {"action": "ASSERT_OCCUPIED_SLOTS", "count": 1}


def test_bad_http_code_pairs_stay_outside_allowlist():
    allowed = {
        (e["httpStatus"], e["aiCode"]) for e in read_json(ROOT / "error-mapping.json")["mappings"]
    }
    for case in HTTP:
        if not case["caseId"].startswith("H-unknown-error-"):
            continue
        response = case["setup"]["injectedResponse"]
        assert (response["httpStatus"], response["body"]["code"]) not in allowed
        assert case["expected"]["be"]["failureCode"] == "AI_SERVICE_UNAVAILABLE"


def test_error_wire_boundaries_use_valid_json_and_preserve_mapping_below_limit():
    by_id = {c["caseId"]: c for c in HTTP}
    for suffix, delta in (("below", -1), ("at", 0), ("above", 1)):
        case_id = "H-be-oversized-error" if delta == 1 else f"H-be-error-bytes-{suffix}"
        case = by_id[case_id]
        raw = payload(case["setup"]["bodyRecipe"])
        assert len(raw) == LIMITS["response.bytes"] + delta
        assert set(json.loads(raw)) == {"code", "message"}
        assert json.loads(raw)["code"] == "AI_OVERLOADED"
        assert case["setup"]["injectedResponseStatus"] == 503
        assert case["expected"]["be"]["failureCode"] == (
            "AI_INVALID_RESPONSE" if delta == 1 else "AI_SERVICE_UNAVAILABLE"
        )
