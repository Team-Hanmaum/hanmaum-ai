"""Check the shared examples themselves, not the unimplemented v2 endpoint/store."""

import hashlib
from copy import deepcopy
from datetime import date, datetime, time
from pathlib import PurePosixPath, PureWindowsPath
from uuid import UUID

import pytest

from scripts.fixture_manifest import (
    DEFAULT_ROOT,
    SUITES,
    build_manifest,
    bundle_path,
    inventory_sort_key,
    read_json,
)

ROOT = DEFAULT_ROOT
CASES = {name: read_json(ROOT / path) for name, path in SUITES.items()}
ALL_CASES = [case for suite in CASES.values() for case in suite]
MAPPING = {
    (entry["httpStatus"], entry["aiCode"]): entry["beFailureCode"]
    for entry in read_json(ROOT / "error-mapping.json")["mappings"]
}
FIELDS = {
    "SCHEDULE": {"title", "description", "status", "scheduledDate", "scheduledTime", "location"},
    "TASK": {"title", "description", "status", "dueDate", "assigneeMembershipId"},
    "QUESTION": {"title", "description", "status", "answerText"},
    "OBSERVATION": {"title", "description", "observedDate", "observedTimeText"},
    "GUIDANCE": {"title", "description", "sourceName", "receivedDate"},
}
RELATIONS = {
    "CREATE": {"NEW"},
    "UPDATE": {"RELATED", "CONFLICT", "UNDETERMINED"},
    "RESOLVE": {"RELATED", "CONFLICT", "UNDETERMINED"},
    "LINK_EVIDENCE": {"DUPLICATE", "UNDETERMINED"},
}
SUGGESTION_KEYS = {
    "operation",
    "type",
    "targetItemId",
    "expectedVersion",
    "candidateItemIds",
    "title",
    "evidence",
    "changes",
    "clearFields",
    "relationKind",
    "reviewReasons",
    "basisReferences",
}


def payload(relative):
    return read_json(bundle_path(ROOT, relative))


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


@pytest.mark.parametrize("path_type", [PurePosixPath, PureWindowsPath])
def test_manifest_inventory_order_is_portable(path_type):
    # Fixed published order, including a directory/file-prefix boundary and case ties.
    expected = [
        ".gitattributes",
        "cases/A.json",
        "cases/a.json",
        "cases/backend.json",
        "cases.json",
        "payloads/requests/base.json",
        "README.md",
    ]
    for names in (expected, list(reversed(expected))):
        ordered = sorted(map(path_type, names), key=inventory_sort_key)
        assert [path.as_posix() for path in ordered] == expected


def test_manifest_exact_inventory_and_hashes():
    manifest = read_json(ROOT / "manifest.json")
    assert manifest == build_manifest(ROOT, manifest["revision"])
    assert manifest["revision"].startswith("v2-")
    assert len(manifest["caseIds"]) == len(ALL_CASES)
    for item in manifest["files"]:
        raw = bundle_path(ROOT, item["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        assert b"\r" not in raw, item["path"]
        assert not raw.startswith(b"\xef\xbb\xbf"), item["path"]
        raw.decode("utf-8", errors="strict")


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case["caseId"])
def test_case_metadata_and_references(case):
    assert case["contractVersion"] == "v2"
    assert case["purpose"].strip()
    assert case["contractSections"] and set(case["contractSections"]) <= set(range(1, 16))
    assert case["expected"]
    for obj in walk(case):
        for key, value in obj.items():
            if key.endswith("File"):
                assert bundle_path(ROOT, value).is_file()
        if "method" in obj:
            if obj is case.get("publicQuery"):
                assert case["category"] == "BACKEND"
                assert obj["method"] == "GET"
            else:
                assert obj["method"] == "POST"
                assert obj["path"] == "/v2/analyses"
                assert obj["headers"]["Content-Type"] == "application/json"
                assert "bodyFile" in obj
        if "ai" in obj and "be" in obj and "code" in obj["ai"]:
            ai, be = obj["ai"], obj["be"]
            assert MAPPING[(ai["httpStatus"], ai["code"])] == be["failureCode"]
            assert be["state"] == "FAILED" and be["storedProposalCount"] == 0
            assert be["queryHttpStatus"] == 200


def assert_positive_examples(request, response):
    """Catch transcription errors in positive goldens without invoking app code."""
    assert UUID(response["requestId"]) == UUID(request["requestId"])
    candidates = {UUID(c["itemId"]): c for c in request["candidates"]}
    for candidate in candidates.values():
        values = candidate["currentValues"]
        assert set(values) == FIELDS[candidate["type"]]
        assert type(candidate["version"]) is int
        for key, value in values.items():
            assert value is None or isinstance(value, str)
            if value is not None and key.endswith("Date"):
                assert date.fromisoformat(value).isoformat() == value
            if value is not None and key == "scheduledTime":
                assert time.fromisoformat(value).strftime("%H:%M") == value
    for suggestion in response["suggestions"]:
        assert set(suggestion) == SUGGESTION_KEYS
        operation = suggestion["operation"]
        kind = suggestion["type"]
        assert suggestion["relationKind"] in RELATIONS[operation]
        assert suggestion["evidence"].strip()
        assert suggestion["evidence"] in request["content"]
        reasons = suggestion["reviewReasons"]
        assert len(reasons) == len(set(reasons))
        target = suggestion["targetItemId"]
        candidate_ids = [UUID(i) for i in suggestion["candidateItemIds"]]
        assert len(candidate_ids) == len(set(candidate_ids))
        if target:
            candidate = candidates[UUID(target)]
            assert kind == candidate["type"]
            assert suggestion["expectedVersion"] == candidate["version"]
            assert not candidate_ids
        else:
            assert suggestion["expectedVersion"] is None
        if operation == "CREATE":
            assert target is None and not candidate_ids
            assert not {"TARGET_AMBIGUOUS", "TARGET_NOT_FOUND"}.intersection(reasons)
        elif target is None:
            assert ("TARGET_AMBIGUOUS" in reasons) == bool(candidate_ids)
            assert ("TARGET_NOT_FOUND" in reasons) == (not candidate_ids)
        if suggestion["relationKind"] == "UNDETERMINED":
            assert reasons
        changes = suggestion["changes"]
        clear = suggestion["clearFields"]
        assert all(isinstance(v, str) and v.strip() for v in changes.values())
        assert not set(changes).intersection(clear)
        assert len(clear) == len(set(clear))
        assert "assigneeMembershipId" not in changes and "assigneeMembershipId" not in clear
        if clear:
            assert operation == "UPDATE"
        if operation == "RESOLVE":
            assert kind == "QUESTION" and set(changes) == {"answerText"} and not clear
        elif operation == "LINK_EVIDENCE":
            assert not changes and not clear
        else:
            assert set(changes) <= FIELDS[kind] - {"answerText", "assigneeMembershipId"}
        if operation == "UPDATE" and not changes and not clear:
            assert {"ASSIGNEE_UNRESOLVED", "VALUE_UNCLEAR"}.intersection(reasons)
        if "ASSIGNEE_UNRESOLVED" in reasons:
            assert kind == "TASK"
        refs = {}
        for ref in suggestion["basisReferences"]:
            item_id = UUID(ref["itemId"])
            assert item_id not in refs
            basis_ids = [UUID(i) for i in ref["basisIds"]]
            assert basis_ids and len(set(basis_ids)) == len(basis_ids)
            assert set(basis_ids) <= {UUID(b["basisId"]) for b in candidates[item_id]["basis"]}
            refs[item_id] = basis_ids
        if target:
            assert UUID(target) in refs
        for item_id in candidate_ids:
            assert candidates[item_id]["type"] == kind and item_id in refs
        if suggestion["relationKind"] == "CONFLICT":
            assert "CONFLICT" in reasons and refs


@pytest.mark.parametrize("case", CASES["STATIC"], ids=lambda case: case["caseId"])
def test_static_expectations(case):
    expected = case["expected"]
    ai, be = expected["ai"], expected["be"]
    if ai["httpStatus"] == 200:
        request = payload(case["request"]["bodyFile"])
        response = payload(ai["bodyFile"])
        assert ai["providerCalls"] == 1
        assert case["provider"]["bodyFile"] == ai["bodyFile"]
        assert be["storedProposalCount"] == len(response["suggestions"])
        assert be["state"] == "COMPLETED"
        assert_positive_examples(request, response)
    elif ai["code"] in {
        "INVALID_REQUEST",
        "UNAUTHORIZED",
        "REQUEST_ID_MISMATCH",
        "AI_NOT_CONFIGURED",
    }:
        assert ai["providerCalls"] == 0
    else:
        assert ai["providerCalls"] == 1
        assert be["storedProposalCount"] == 0


def comparison_example(relative):
    """Fixture-only check of pair construction; not the runtime HMAC implementation."""
    data = deepcopy(payload(relative))
    for key in ("requestId", "careSpaceId", "recordId"):
        data[key] = str(UUID(data[key]))
    data["recordedAt"] = datetime.fromisoformat(data["recordedAt"])
    for candidate in data["candidates"]:
        candidate["itemId"] = str(UUID(candidate["itemId"]))
        member = candidate["currentValues"].get("assigneeMembershipId")
        if member:
            candidate["currentValues"]["assigneeMembershipId"] = str(UUID(member))
        for basis in candidate["basis"]:
            basis["basisId"] = str(UUID(basis["basisId"]))
    return data


@pytest.mark.parametrize("case", CASES["RECEIPT"], ids=lambda case: case["caseId"])
def test_receipt_scenario_consistency(case):
    calls = 0
    for step in case["steps"]:
        assert step["action"] in {
            "SUBMIT",
            "SUBMIT_CONCURRENT",
            "FAIL_RECEIPT_STORE",
            "RESTART_AI_PRESERVE_RECEIPTS",
            "ROTATE_COMPARISON_METADATA",
        }
        if step["action"] == "SUBMIT":
            calls += step["expected"]["ai"]["providerCalls"]
        elif step["action"] == "SUBMIT_CONCURRENT":
            assert step["instanceCount"] >= 2 and step["expected"]["orderIndependent"]
            calls += sum(result["providerCalls"] for result in step["expected"]["aiResults"])
    assert calls == case["expected"]["totalProviderCalls"]
    if "bodyEquality" in case:
        left, right = [s["request"]["bodyFile"] for s in case["steps"] if s["action"] == "SUBMIT"]
        assert (comparison_example(left) == comparison_example(right)) is case["bodyEquality"]
        expected_code = "REQUEST_ALREADY_RECEIVED" if case["bodyEquality"] else "REQUEST_ID_REUSED"
        assert case["steps"][-1]["expected"]["ai"]["code"] == expected_code
        assert (ROOT / left).read_bytes() != (ROOT / right).read_bytes()


def test_raw_duplicate_keys_and_precision_are_preserved():
    for name in ("duplicate-root-key", "duplicate-nested-key"):
        with pytest.raises(ValueError, match="Duplicate"):
            payload(f"payloads/requests/invalid-{name}.json")
    for name, timestamp in (("fraction-seven", ".1234567"), ("fraction-seven-zero", ".1234560")):
        raw = (ROOT / f"payloads/requests/invalid-{name}.json").read_text(encoding="utf-8")
        assert timestamp in raw
    escaped = (ROOT / "payloads/requests/compare-unicode-escape.json").read_text(encoding="utf-8")
    assert "\\u" in escaped
    assert payload("payloads/requests/compare-unicode-escape.json") == payload(
        "payloads/requests/base.json"
    )


def test_required_categories_and_error_mapping_coverage():
    assert set(MAPPING) == {
        (400, "REQUEST_ID_MISMATCH"),
        (401, "UNAUTHORIZED"),
        (422, "INVALID_REQUEST"),
        (409, "REQUEST_ALREADY_RECEIVED"),
        (409, "REQUEST_ID_REUSED"),
        (502, "INVALID_AI_RESPONSE"),
        (502, "AI_UNAVAILABLE"),
        (503, "AI_NOT_CONFIGURED"),
        (503, "RECEIPT_STORE_UNAVAILABLE"),
        (504, "AI_TIMEOUT"),
    }
    outcomes = {
        (obj["httpStatus"], obj["code"])
        for case in ALL_CASES
        for obj in walk(case.get("expected", {}))
        if "code" in obj
    }
    outcomes |= {
        (step["expected"]["ai"]["httpStatus"], step["expected"]["ai"]["code"])
        for case in CASES["RECEIPT"]
        for step in case["steps"]
        if "code" in step.get("expected", {}).get("ai", {})
    }
    assert set(MAPPING) <= outcomes
    ids = {case["caseId"] for case in ALL_CASES}
    assert {
        "A-all-current-values",
        "A-resolve-answer",
        "A-existing-answer",
        "A-ambiguous-two",
        "A-create-compared",
        "A-not-found-update",
        "A-response-mixed",
        "B-concurrent",
        "B-restart",
        "B-contract-version-key",
        "B-uncomparable-record",
        "C-unreferenced-basis",
        "C-storage-rollback",
        "C-invalid-ai-200",
        "C-query-invalidated",
        "C-requester-inactive",
        "C-fixed-run-version",
        "C-explicit-retry",
        "C-delete-derived-copies",
    } <= ids


@pytest.mark.parametrize("case", CASES["BACKEND"], ids=lambda case: case["caseId"])
def test_backend_scenario_consistency(case):
    request = payload(case["request"]["bodyFile"])
    be = case["expected"]["be"]
    query = case["publicQuery"]
    assert query["pathTemplate"] == (
        "/api/spaces/{spaceId}/source-records/{recordId}/analyses/{analysisId}"
    )
    assert query["spaceId"] == request["careSpaceId"]
    assert query["recordId"] == request["recordId"]
    assert query["analysisIdFrom"] == "CURRENT_RUN"
    assert query["phase"] == "AFTER_SCENARIO"
    requester_inactive = any(e["action"] == "END_REQUESTER_MEMBERSHIP" for e in case["events"])
    assert query["viewer"] == (
        "OTHER_ACTIVE_SPACE_MEMBER" if requester_inactive else "ACTIVE_SPACE_MEMBER"
    )
    source_deleted = any(
        e["action"] == "DELETE_SOURCE_RECORD" and e["recordId"] == request["recordId"]
        for e in case["events"]
    )
    assert be["queryHttpStatus"] == (404 if source_deleted else 200)
    assert be.get("queryErrorCode") == ("RESOURCE_NOT_FOUND" if source_deleted else None)
    if be.get("state") in {"FAILED", "DISCARDED"}:
        assert be["storedProposalCount"] == 0
    for event in case["events"]:
        assert event["phase"] in {
            "BEFORE_ACCEPTANCE",
            "AFTER_ACCEPTANCE",
            "BEFORE_COMPLETION",
            "DURING_STORAGE",
            "AFTER_COMPLETION",
        }
        assert event["action"] in {
            "DELETE_SOURCE_RECORD",
            "DETACH_BASIS",
            "INVALIDATE_DIRECT_DOCUMENT",
            "CHANGE_CANDIDATE_VERSION",
            "END_REQUESTER_MEMBERSHIP",
            "FAIL_PROPOSAL_INSERT",
            "CHANGE_ACTIVE_CONTRACT",
            "SEED_FAILED_RUN",
        }
        if event["action"] in {"DETACH_BASIS", "INVALIDATE_DIRECT_DOCUMENT"}:
            assert_basis_invalidation(case, request, event)
        if event["action"] == "DELETE_SOURCE_RECORD":
            assert event["recordId"] == request["recordId"]
            if event["phase"] == "BEFORE_COMPLETION":
                assert be["state"] == "DISCARDED" and be["storedProposalCount"] == 0
            else:
                assert event["phase"] == "AFTER_COMPLETION"
                assert be["completionState"] == "COMPLETED"
                assert "state" not in be  # No newly prescribed post-deletion state.
                assert be["deletedSourceTextVisible"] is False
                assert be["deletedSourceTextStoredInDerivedCopies"] is False
    if case["aiResponse"]["httpStatus"] == 200 and be.get("failureCode") != "AI_INVALID_RESPONSE":
        response = payload(case["aiResponse"]["bodyFile"])
        assert_positive_examples(request, response)
        if be.get("state") == "COMPLETED":
            assert len(response["suggestions"]) == be["storedProposalCount"]
        if be.get("completionState") == "COMPLETED":
            assert len(response["suggestions"]) == be["storedAtCompletion"]


def assert_basis_invalidation(case, request, event):
    """Validate scenario setup/expectations, without claiming to execute BE mutations."""
    candidate = next(c for c in request["candidates"] if c["itemId"] == event["itemId"])
    basis = next(b for b in candidate["basis"] if b["basisId"] == event["basisId"])
    assert basis["kind"] == event["basisKind"]
    assert "keepBasisRow" not in event and "keepDocument" not in event
    detach = event["action"] == "DETACH_BASIS"
    if detach:
        assert event["removeCareItemBasisRow"] is True
        assert event["preserveBasisContent"] is True
    else:
        assert basis["kind"] == "DIRECT_INPUT"
        assert event["redactBody"] is True
        assert event["retainCareItemBasisRow"] is True

    be = case["expected"]["be"]
    if event["phase"] == "BEFORE_COMPLETION":
        assert be["state"] == "DISCARDED" and be["storedProposalCount"] == 0
    else:
        assert event["phase"] == "AFTER_COMPLETION"
        assert be["completionState"] == "COMPLETED" and be["storedAtCompletion"] > 0
        assert be["visibleValidProposalCount"] == be["visibleValidBasisCount"] == 0
    assert be["queryHttpStatus"] == 200

    if basis["kind"] == "DIRECT_INPUT":
        seed = next(
            s for s in case["setup"]["directInputDocuments"] if s["basisId"] == basis["basisId"]
        )
        assert seed["itemId"] == candidate["itemId"]
        # Separate current and original documents so invalidating the original cannot
        # accidentally pass by checking only current_document_id or the item version.
        ids = [seed[k] for k in ("itemId", "basisId", "currentDocumentId", "basisDocumentId")]
        assert len({UUID(value) for value in ids}) == len(ids)
        if not detach:
            assert event["documentId"] == seed["basisDocumentId"]
        assert be["currentDocumentUnchanged"] is True
        assert be["candidateVersionUnchanged"] is True
        assert be["basisAfterEvent"] == {
            "careItemBasisRowExists": not detach,
            "documentExists": True,
            "documentValid": detach,
        }


def test_direct_input_invalidation_coverage_includes_unreferenced_inputs():
    covered = set()
    for case in CASES["BACKEND"]:
        for event in case["events"]:
            if event.get("basisKind") != "DIRECT_INPUT":
                continue
            response = payload(case["aiResponse"]["bodyFile"])
            references = {
                (ref["itemId"], basis_id)
                for suggestion in response["suggestions"]
                for ref in suggestion["basisReferences"]
                for basis_id in ref["basisIds"]
            }
            referenced = (event["itemId"], event["basisId"]) in references
            if not referenced:
                # The unreferenced dependency belongs to another input candidate.
                assert event["itemId"] not in {s["targetItemId"] for s in response["suggestions"]}
                assert event["itemId"] not in {item_id for item_id, _ in references}
            covered.add((event["phase"], event["action"], referenced))
    assert covered == {
        (phase, action, referenced)
        for phase in ("BEFORE_COMPLETION", "AFTER_COMPLETION")
        for action in ("DETACH_BASIS", "INVALIDATE_DIRECT_DOCUMENT")
        for referenced in (True, False)
    }


def test_backend_goldens_retain_all_operations_and_retry_identity():
    cases = {case["caseId"]: case for case in CASES["BACKEND"]}
    stored = payload(cases["C-completed"]["expected"]["be"]["storedSuggestionsFile"])
    assert {p["operation"] for p in stored["suggestions"]} == set(RELATIONS)
    assert_positive_examples(payload(cases["C-completed"]["request"]["bodyFile"]), stored)
    retry = cases["C-explicit-retry"]
    request = payload(retry["request"]["bodyFile"])
    assert request["requestId"] != retry["events"][0]["requestId"]
    assert request["recordedAt"] == retry["events"][0]["recordedAt"]
    assert request["requestId"] == retry["request"]["headers"]["X-Request-Id"]
    assert_positive_examples(request, payload(retry["aiResponse"]["bodyFile"]))


def test_no_unreferenced_payloads():
    referenced = {
        value
        for case in ALL_CASES
        for obj in walk(case)
        for key, value in obj.items()
        if key.endswith("File")
    }
    actual = {path.relative_to(ROOT).as_posix() for path in (ROOT / "payloads").rglob("*.json")}
    assert actual == referenced


def test_metadata_loader_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"caseId":"one","caseId":"two"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        read_json(path)


def test_bundle_references_cannot_escape_root():
    with pytest.raises(ValueError, match="outside"):
        bundle_path(ROOT, "../analysis-request.json")
