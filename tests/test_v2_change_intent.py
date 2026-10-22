"""Offline facts and mutation checks for the focused change-intent regression."""

import json

import pytest
from test_v2_holdout import handcrafted_response

from app.v2.validation import parse_request, validate_response
from evals.v2_cases import grade
from evals.v2_change_intent import REVISION, cases
from scripts.evaluate_v2 import digest


def answer(case):
    if case["caseId"].startswith("holdout-"):
        return handcrafted_response(case)
    candidates = case["request"]["candidates"]
    if candidates:
        item = candidates[0]
        suggestion = {
            "operation": "UPDATE",
            "type": "SCHEDULE",
            "targetItemId": item["itemId"],
            "expectedVersion": item["version"],
            "relationKind": "UNDETERMINED",
            "reviewReasons": ["VALUE_UNCLEAR"],
            "changes": {},
            "basisReferences": [
                {"itemId": item["itemId"], "basisIds": [item["basis"][0]["basisId"]]}
            ],
        }
    else:
        changes = {"sourceName": "가상센터"}
        if case["caseId"] == "create-guidance-received-date":
            changes["receivedDate"] = "2026-10-22"
        suggestion = {
            "operation": "CREATE",
            "type": "GUIDANCE",
            "targetItemId": None,
            "expectedVersion": None,
            "relationKind": "NEW",
            "reviewReasons": [],
            "changes": changes,
            "basisReferences": [],
        }
    suggestion.update(
        candidateItemIds=[], clearFields=[], title="합성 검토", evidence=case["request"]["content"]
    )
    return {"requestId": case["request"]["requestId"], "suggestions": [suggestion]}


@pytest.mark.parametrize("case", cases(), ids=lambda c: c["caseId"])
def test_handwritten_answers_satisfy_contract_and_facts(case):
    request = parse_request(json.dumps(case["request"]))
    assert grade(case, validate_response(request, answer(case))) == []


@pytest.mark.parametrize(
    "case_id,field",
    [
        ("holdout-date-change-without-replacement", "scheduledDate"),
        ("change-intent-time-unknown", "scheduledTime"),
        ("change-intent-location-unknown", "location"),
    ],
)
def test_unstated_removal_is_contract_valid_but_fails_meaning(case_id, field):
    case = next(c for c in cases() if c["caseId"] == case_id)
    request = parse_request(json.dumps(case["request"]))
    payload = answer(case)
    payload["suggestions"][0]["clearFields"] = [field]
    assert "clearFields" in grade(case, validate_response(request, payload))
    payload["suggestions"][0]["clearFields"] = []
    payload["suggestions"][0]["changes"][field] = case["request"]["candidates"][0]["currentValues"][
        field
    ]
    assert "absent." + field in grade(case, validate_response(request, payload))


def test_explicit_removal_cannot_be_replaced_with_unknown_review():
    case = next(c for c in cases() if c["caseId"] == "holdout-direct-input-explicit-clear")
    payload = answer(case)
    payload["suggestions"][0]["clearFields"] = []
    payload["suggestions"][0]["reviewReasons"] = ["VALUE_UNCLEAR"]
    result = validate_response(parse_request(json.dumps(case["request"])), payload)
    assert "clearFields" in grade(case, result)


def test_explicit_field_expectations_are_pinned():
    assert REVISION == "synthetic-change-intent-v2-20261023.2"
    assert len(cases()) == 6
    assert digest(cases()) == "5802e3ad08acc083dda1eb5f5b2c13b9574223565876e2dfa34ab69ab887f61c"
