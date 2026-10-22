"""Offline holdout checks; handcrafted responses never call an LLM or read its output."""

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.v2.validation import parse_request, validate_response
from evals.v2_cases import grade, uid
from evals.v2_holdout import REVISION, cases


def handcrafted_response(case):
    """Facts written independently of expected dictionaries, with valid wire metadata."""

    def suggestion(
        operation,
        kind,
        changes,
        *,
        target=None,
        relation="NEW",
        candidates=(),
        reasons=(),
        clear=(),
    ):
        targets = [uid(target)] if target else [uid(number) for number in candidates]
        referenced = [item for item in case["request"]["candidates"] if item["itemId"] in targets]
        return {
            "operation": operation,
            "type": kind,
            "targetItemId": uid(target) if target else None,
            "expectedVersion": 3 if target else None,
            "candidateItemIds": [uid(number) for number in candidates],
            "title": "합성 검토 제안",
            "evidence": case["request"]["content"],
            "changes": changes,
            "clearFields": list(clear),
            "relationKind": relation,
            "reviewReasons": list(reasons),
            "basisReferences": [
                {"itemId": item["itemId"], "basisIds": [item["basis"][0]["basisId"]]}
                for item in referenced
            ],
        }

    responses = {
        "holdout-two-independent-appointments": [
            suggestion(
                "CREATE",
                "SCHEDULE",
                {
                    "title": "도예 수업",
                    "scheduledDate": "2026-11-17",
                    "scheduledTime": "09:00",
                    "location": "별빛공방 3층",
                },
            ),
            suggestion(
                "CREATE",
                "SCHEDULE",
                {
                    "title": "독서 모임",
                    "scheduledDate": "2026-11-19",
                    "scheduledTime": "16:00",
                    "location": "솔잎도서관 1층",
                },
            ),
        ],
        "holdout-answer-and-observation": [
            suggestion(
                "RESOLVE",
                "QUESTION",
                {"answerText": "앞치마 한 벌만 챙기면 됩니다."},
                target=72001,
                relation="RELATED",
            ),
            suggestion(
                "CREATE",
                "OBSERVATION",
                {
                    "description": "퍼즐을 30분 동안 집중해서 맞춤",
                    "observedDate": "2026-11-13",
                    "observedTimeText": "오전",
                },
            ),
        ],
        "holdout-date-change-without-replacement": [
            suggestion(
                "UPDATE",
                "SCHEDULE",
                {},
                target=72002,
                relation="UNDETERMINED",
                reasons=["VALUE_UNCLEAR"],
            )
        ],
        "holdout-two-matching-targets": [
            suggestion(
                "UPDATE",
                "SCHEDULE",
                {"scheduledTime": "14:00"},
                relation="UNDETERMINED",
                candidates=[72003, 72004],
                reasons=["TARGET_AMBIGUOUS"],
            )
        ],
        "holdout-identical-observation": [
            suggestion("LINK_EVIDENCE", "OBSERVATION", {}, target=72005, relation="DUPLICATE")
        ],
        "holdout-contradicting-start-time": [
            suggestion(
                "UPDATE",
                "SCHEDULE",
                {"scheduledTime": "11:00"},
                target=72006,
                relation="CONFLICT",
                reasons=["CONFLICT"],
            )
        ],
        "holdout-direct-input-task-completed": [
            suggestion("UPDATE", "TASK", {"status": "COMPLETED"}, target=72007, relation="RELATED")
        ],
        "holdout-direct-input-explicit-clear": [
            suggestion(
                "UPDATE", "SCHEDULE", {}, target=72008, relation="RELATED", clear=["location"]
            )
        ],
        "holdout-untrusted-instruction-in-source": [
            suggestion(
                "CREATE",
                "OBSERVATION",
                {
                    "description": "색칠 활동을 15분 동안 함",
                    "observedDate": "2026-11-13",
                    "observedTimeText": "오후",
                },
            )
        ],
        "holdout-guidance-explicit-received-day": [
            suggestion(
                "CREATE",
                "GUIDANCE",
                {
                    "sourceName": "솔바람문화관",
                    "description": "이용할 때 개인 컵 가져오기",
                    "receivedDate": "2026-11-12",
                },
            )
        ],
        "holdout-guidance-received-day-unknown": [
            suggestion(
                "CREATE",
                "GUIDANCE",
                {"sourceName": "솔바람문화관", "description": "이용할 때 개인 컵 가져오기"},
            )
        ],
        "holdout-relative-day-across-utc-boundary": [
            suggestion(
                "CREATE",
                "SCHEDULE",
                {
                    "title": "산책 모임",
                    "scheduledDate": "2026-11-14",
                    "scheduledTime": "06:00",
                    "location": "달빛정원 서문",
                },
            )
        ],
    }
    return {"requestId": case["request"]["requestId"], "suggestions": responses[case["caseId"]]}


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
