"""Offline holdout checks; handcrafted responses never call an LLM or read its output."""

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.v2.validation import parse_request, validate_response
from evals.v2_cases import grade
from evals.v2_holdout import REVISION, cases
from tests.support.v2_answers import handcrafted_response


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["caseId"])
def test_holdout_requests_are_valid_immutable_wire_snapshots(case):
    source = deepcopy(case["request"])
    snapshot = parse_request(json.dumps(source, ensure_ascii=False))
    assert snapshot.model_dump(mode="json", by_alias=True) == source
    source["content"] = "caller mutation"
    assert snapshot.content == case["request"]["content"]
    with pytest.raises(ValidationError):
        snapshot.content = "replacement"
    if snapshot.candidates:
        original_title = snapshot.candidates[0].current_values.title
        source["candidates"][0]["currentValues"]["title"] = "nested mutation"
        assert snapshot.candidates[0].current_values.title == original_title
        with pytest.raises(ValidationError):
            snapshot.candidates[0].current_values.title = "replacement"


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["caseId"])
def test_handcrafted_holdout_answers_pass_contract_and_semantic_checks(case):
    request = parse_request(json.dumps(case["request"]))
    response = validate_response(request, handcrafted_response(case))
    assert grade(case, response) == []


def test_two_appointments_match_without_order_and_cannot_reuse_one_suggestion():
    case = cases()[0]
    request = parse_request(json.dumps(case["request"]))
    response = handcrafted_response(case)
    response["suggestions"].reverse()
    assert grade(case, validate_response(request, response)) == []
    response["suggestions"][1] = deepcopy(response["suggestions"][0])
    assert grade(case, validate_response(request, response))


@pytest.mark.parametrize(
    "case_id", ["holdout-guidance-received-day-unknown", "holdout-date-change-without-replacement"]
)
def test_grader_rejects_fabricated_dates_even_when_wire_contract_allows_them(case_id):
    case = next(item for item in cases() if item["caseId"] == case_id)
    request = parse_request(json.dumps(case["request"]))
    response = handcrafted_response(case)
    field = "receivedDate" if "guidance" in case_id else "scheduledDate"
    response["suggestions"][0]["changes"][field] = "2026-11-13"
    assert grade(case, validate_response(request, response))


@pytest.mark.parametrize(
    "case_id", ["holdout-date-change-without-replacement", "holdout-two-matching-targets"]
)
def test_contract_permitted_related_relationship_does_not_fail_ambiguous_fact_checks(case_id):
    case = next(item for item in cases() if item["caseId"] == case_id)
    request = parse_request(json.dumps(case["request"]))
    response = handcrafted_response(case)
    response["suggestions"][0]["relationKind"] = "RELATED"
    assert grade(case, validate_response(request, response)) == []


def test_holdout_revision_is_unique_and_dataset_is_reproducible():
    first = cases()
    assert REVISION == "synthetic-holdout-v2-20261023.2"
    assert len(first) == len({case["caseId"] for case in first}) == 12
    assert len({case["request"]["requestId"] for case in first}) == 12
    assert first == cases()
    # Pin the explicit field allowlist revision; original fact preservation is tested separately.
    digest = hashlib.sha256(
        json.dumps(first, ensure_ascii=True, sort_keys=True).encode()
    ).hexdigest()
    assert digest == "bc1ad13dd473b40c48352d24a9d7b7e1982555053bb677153ba230b2660800d6"
