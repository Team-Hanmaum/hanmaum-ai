"""Grader false positives and incomplete cache metrics; no paid API calls."""

import json
from copy import deepcopy

import pytest
from test_v2_change_intent import answer
from test_v2_holdout import handcrafted_response

from app.v2.schemas import CHANGE_FIELDS
from app.v2.validation import parse_request, validate_response
from evals.v2_cases import cases, grade
from evals.v2_holdout import cases as holdout_cases
from scripts.evaluate_v2 import dataset, digest, summarize


@pytest.mark.parametrize("field,value", [("status", "COMPLETED"), ("description", "추가 변경")])
def test_unrequested_update_fields_fail_even_without_explicit_absence(field, value):
    case = next(c for c in holdout_cases() if c["caseId"] == "holdout-contradicting-start-time")
    request = parse_request(json.dumps(case["request"]))
    payload = handcrafted_response(case)
    assert grade(case, validate_response(request, payload)) == []
    assert field not in case["expected"][0]["absentChanges"]
    payload["suggestions"][0]["changes"][field] = value
    assert "unexpected." + field in grade(case, validate_response(request, payload))


def test_unrequested_create_status_cannot_hide_in_multi_suggestion_matching():
    case = holdout_cases()[0]
    request = parse_request(json.dumps(case["request"]))
    payload = handcrafted_response(case)
    payload["suggestions"].reverse()
    assert grade(case, validate_response(request, payload)) == []
    payload["suggestions"][0]["changes"]["status"] = "COMPLETED"
    assert grade(case, validate_response(request, payload)) == ["unmatched_expected.1"]


def test_optional_source_based_prose_remains_allowed_without_opening_all_fields():
    case = next(c for c in cases() if c["caseId"] == "create-guidance")
    request = parse_request(json.dumps(case["request"]))
    payload = answer(case)
    payload["suggestions"][0]["changes"].update(
        title="실내화 준비 안내", description="방문 시 실내화를 가져오라는 안내"
    )
    assert grade(case, validate_response(request, payload)) == []
    payload["suggestions"][0]["changes"]["receivedDate"] = "2026-10-22"
    assert "absent.receivedDate" in grade(case, validate_response(request, payload))


@pytest.mark.parametrize(
    "suite,old_hash,new_hash",
    [
        (
            "regression",
            "4afea06e16f7c6b1fa95479564c06fd9b8aee96a0042b9114567b68ccda890eb",
            "ae93826ad2d209e053702ad37dbdbda4fde20b2b150fc4a4e708ea0d39e6f7a8",
        ),
        (
            "holdout",
            "09ef0ad8e6b5325745248841209cec0a87fa22f3cf64e42f70db37bd09183427",
            "bc1ad13dd473b40c48352d24a9d7b7e1982555053bb677153ba230b2660800d6",
        ),
        (
            "change-intent",
            "4c03f05748d7868c73ce735566a86ec875d752eb921a26362c5614b994069b57",
            "5802e3ad08acc083dda1eb5f5b2c13b9574223565876e2dfa34ab69ab887f61c",
        ),
        (
            "change-holdout",
            "2fe921ebff78f163c109327e81644ea8cadef3d3bd694ce0fb3f16368041fea8",
            "4c80fbec2b04139aad4048cedf2c870545144dc4beee073d8d244985a0a433f7",
        ),
    ],
)
def test_revision_adds_explicit_allowlists_without_rewriting_prior_facts(suite, old_hash, new_hash):
    revision, current = dataset(suite)
    assert revision.endswith("20261023.2") and digest(current) == new_hash
    old = deepcopy(current)
    for case in old:
        for item in case["expected"]:
            optional = set(item.pop("optionalChanges"))
            allowed = optional | set(item["changes"]) | set(item["contains"])
            assert optional <= CHANGE_FIELDS[item["type"]]
            assert not allowed & set(item["absentChanges"])
            assert not optional & (set(item["changes"]) | set(item["contains"]))
    assert digest(old) == old_hash


def row(usage, calls=1):
    return {
        "elapsedSeconds": 1,
        "contractPassed": True,
        "semanticPassed": True,
        "providerCalls": calls,
        "provider": usage,
    }


@pytest.mark.parametrize("cache", [None, -1, True, "0", 1.5])
def test_unknown_or_invalid_cache_is_not_reported_as_zero(cache):
    result = summarize([row({"input_tokens": 100, "output_tokens": 20, "cached_tokens": cache})])
    assert result["cachedInputTokens"] is None
    assert result["zeroCachedInputCalls"] == 0
    assert result["cacheUsageMissingCalls"] == result["usageMissingCalls"] == 1


def test_absent_cache_details_and_partially_known_cache_remain_unknown():
    result = summarize(
        [
            row({"input_tokens": 100, "output_tokens": 20, "cached_tokens": 50}),
            row({"input_tokens": 100, "output_tokens": 20}),
            row(None),
        ]
    )
    assert result["cachedInputTokens"] is None  # Not a misleading partial total of 50.
    assert result["cacheUsageMissingCalls"] == result["usageMissingCalls"] == 2
    assert result["zeroCachedInputCalls"] == 0


def test_verified_zero_cache_and_output_usage_missing_are_distinct():
    complete = row({"input_tokens": 100, "output_tokens": 20, "cached_tokens": 0})
    result = summarize([complete])
    assert result["cachedInputTokens"] == 0 and result["zeroCachedInputCalls"] == 1
    assert result["cacheUsageMissingCalls"] == result["usageMissingCalls"] == 0
    incomplete = deepcopy(complete)
    incomplete["provider"]["output_tokens"] = None
    result = summarize([incomplete])
    assert result["cachedInputTokens"] == 0 and result["cacheUsageMissingCalls"] == 0
    assert result["usageMissingCalls"] == 1


def test_zero_calls_do_not_invent_missing_cache_usage():
    result = summarize([row(None, calls=0)])
    assert (
        result["cachedInputTokens"]
        == result["cacheUsageMissingCalls"]
        == result["usageMissingCalls"]
        == 0
    )
