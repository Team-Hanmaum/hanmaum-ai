"""Offline contract and semantic sensitivity checks for the independent change holdout."""

import hashlib
import json

import pytest

from app.errors import AnalysisFailure
from app.v2.schemas import CHANGE_FIELDS
from app.v2.validation import parse_request, validate_response
from evals.v2_cases import grade
from evals.v2_change_holdout import REVISION, cases


def handcrafted_response(case):
    changes, clear, reasons, relation = {
        "change-holdout-task-deadline-removed": ({}, ["dueDate"], [], "RELATED"),
        "change-holdout-time-replacement-pending": ({}, [], ["VALUE_UNCLEAR"], "UNDETERMINED"),
        "change-holdout-confirmed-time-pending-place": (
            {"scheduledTime": "16:30"},
            [],
            ["VALUE_UNCLEAR"],
            "RELATED",
        ),
    }[case["caseId"]]
    item = case["request"]["candidates"][0]
    return {
        "requestId": case["request"]["requestId"],
        "suggestions": [
            {
                "operation": "UPDATE",
                "type": item["type"],
                "targetItemId": item["itemId"],
                "expectedVersion": item["version"],
                "candidateItemIds": [],
                "title": "명시된 변경과 미정 값 검토",
                "evidence": case["request"]["content"],
                "changes": changes,
                "clearFields": clear,
                "relationKind": relation,
                "reviewReasons": reasons,
                "basisReferences": [
                    {"itemId": item["itemId"], "basisIds": [item["basis"][0]["basisId"]]}
                ],
            }
        ],
    }


@pytest.mark.parametrize("case", cases(), ids=lambda item: item["caseId"])
def test_change_holdout_request_and_handcrafted_response_satisfy_contract(case):
    snapshot = parse_request(json.dumps(case["request"], ensure_ascii=False))
    assert snapshot.model_dump(mode="json", by_alias=True) == case["request"]
    result = validate_response(snapshot, handcrafted_response(case))
    assert grade(case, result) == []


@pytest.mark.parametrize("case", cases(), ids=lambda item: item["caseId"])
def test_expected_changes_and_omissions_cover_all_allowed_fields(case):
    expected_item = case["expected"][0]
    changed = set(expected_item["changes"])
    omitted = set(expected_item["absentChanges"])
    assert not changed & omitted
    assert changed | omitted == CHANGE_FIELDS[expected_item["type"]]


@pytest.mark.parametrize("case", cases(), ids=lambda item: item["caseId"])
def test_wire_valid_but_unrequested_deletion_fails_semantic_grade(case):
    snapshot = parse_request(json.dumps(case["request"]))
    response = handcrafted_response(case)
    wrong_field = {
        "change-holdout-task-deadline-removed": "description",
        "change-holdout-time-replacement-pending": "scheduledTime",
        "change-holdout-confirmed-time-pending-place": "location",
    }[case["caseId"]]
    response["suggestions"][0]["clearFields"] = [wrong_field]
    result = validate_response(snapshot, response)
    assert "clearFields" in grade(case, result)


@pytest.mark.parametrize("case", cases(), ids=lambda item: item["caseId"])
def test_wire_valid_but_invented_replacement_fails_semantic_grade(case):
    snapshot = parse_request(json.dumps(case["request"]))
    response = handcrafted_response(case)
    suggestion = response["suggestions"][0]
    field, value = {
        "change-holdout-task-deadline-removed": ("dueDate", "2026-12-07"),
        "change-holdout-time-replacement-pending": ("scheduledTime", "16:00"),
        "change-holdout-confirmed-time-pending-place": ("location", "오름회관 2층"),
    }[case["caseId"]]
    suggestion["clearFields"] = []
    suggestion["changes"][field] = value
    result = validate_response(snapshot, response)
    assert f"absent.{field}" in grade(case, result)


@pytest.mark.parametrize("case", cases()[1:], ids=lambda item: item["caseId"])
def test_missing_unclear_reason_fails_contract_or_semantic_validation(case):
    snapshot = parse_request(json.dumps(case["request"]))
    response = handcrafted_response(case)
    suggestion = response["suggestions"][0]
    suggestion["relationKind"] = "RELATED"
    suggestion["reviewReasons"] = []
    if not suggestion["changes"]:
        with pytest.raises(AnalysisFailure) as failure:
            validate_response(snapshot, response)
        assert failure.value.code == "INVALID_AI_RESPONSE"
        return
    result = validate_response(snapshot, response)
    assert "reviewReasons" in grade(case, result)


def test_change_holdout_field_allowlist_revision_is_pinned():
    dataset = cases()
    assert REVISION == "synthetic-change-holdout-v2-20261023.2"
    assert len(dataset) == len({case["caseId"] for case in dataset}) == 3
    assert len({case["request"]["requestId"] for case in dataset}) == 3
    assert dataset == cases()
    digest = hashlib.sha256(
        json.dumps(dataset, ensure_ascii=True, sort_keys=True).encode()
    ).hexdigest()
    assert digest == "4c80fbec2b04139aad4048cedf2c870545144dc4beee073d8d244985a0a433f7"
