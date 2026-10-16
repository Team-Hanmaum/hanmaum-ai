"""Exercise real v2 contract code using the unchanged shared fixture revision."""

import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest

from app.errors import AnalysisFailure
from app.v2.validation import parse_request, validate_response

ROOT = Path(__file__).parent / "fixtures/v2"
STATIC_CASES = json.loads((ROOT / "cases/static.json").read_bytes())

# Exact IDs keep new cases from being silently excluded by status-code filters.
DEFERRED_STATIC = {
    "A-auth-missing": "HTTP authentication",
    "A-auth-invalid": "HTTP authentication",
    "A-header-mismatch": "HTTP header/body ID comparison",
    "A-header-uuid-case": "HTTP header/body UUID comparison",
    "A-unavailable": "Provider invocation and exception mapping",
    "A-disabled": "Provider configuration and invocation",
    "A-timeout": "Provider invocation and timeout",
}
CONTRACT_CASES = [case for case in STATIC_CASES if case["caseId"] not in DEFERRED_STATIC]


def raw(relative):
    return (ROOT / relative).read_bytes()


def request_data():
    return json.loads(raw("payloads/requests/base.json"))


def response_data():
    return json.loads(raw("payloads/responses/update.json"))


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def assert_failure(error, status, code):
    assert error.status_code == status
    assert error.code == code
    assert error.message.strip()


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=lambda c: c["caseId"])
def test_shared_static_contract(case):
    expected = case["expected"]["ai"]
    request_raw = raw(case["request"]["bodyFile"])
    if expected.get("code") == "INVALID_REQUEST":
        with pytest.raises(AnalysisFailure) as caught:
            parse_request(request_raw)
        assert_failure(caught.value, expected["httpStatus"], expected["code"])
        return

    request = parse_request(request_raw)
    assert case["provider"]["mode"] == "RETURN"
    provider_raw = raw(case["provider"]["bodyFile"])
    if expected.get("code") == "INVALID_AI_RESPONSE":
        with pytest.raises(AnalysisFailure) as caught:
            validate_response(request, provider_raw)
        assert_failure(caught.value, expected["httpStatus"], expected["code"])
    else:
        assert expected["httpStatus"] == 200
        result = validate_response(request, provider_raw)
        # Parsing/serialization must not silently drop, synthesize or alter fields/text.
        actual = result.model_dump(mode="json")
        golden = json.loads(raw(expected["bodyFile"]))
        assert normalize_uuids(actual) == normalize_uuids(golden)
        # Future SDK adapters may deliver a decoded dict instead of raw JSON.
        assert validate_response(request, json.loads(provider_raw)) == result


def normalize_uuids(value, key=None):
    """Normalize only declared UUID fields for golden comparison; leave all text alone."""
    if isinstance(value, dict):
        return {k: normalize_uuids(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_uuids(v, key) for v in value]
    if value is not None and key in {
        "requestId",
        "targetItemId",
        "candidateItemIds",
        "itemId",
        "basisIds",
    }:
        return str(UUID(value))
    return value


def test_fixture_coverage_is_explicit():
    manifest = json.loads(raw("manifest.json"))
    assert manifest["revision"] == "v2-20261016.2"
    all_ids = {case["caseId"] for case in STATIC_CASES}
    assert DEFERRED_STATIC.keys() <= all_ids
    assert len(CONTRACT_CASES) == 139
    assert {c["caseId"] for c in CONTRACT_CASES} | DEFERRED_STATIC.keys() == all_ids
    for case in CONTRACT_CASES:
        expected = case["expected"]["ai"]
        assert (expected["httpStatus"], expected.get("code")) in {
            (200, None),
            (422, "INVALID_REQUEST"),
            (502, "INVALID_AI_RESPONSE"),
        }


@pytest.mark.parametrize("precision", range(1, 7))
def test_recorded_at_preserves_supported_precision(precision):
    data = request_data()
    data["recordedAt"] = "2026-10-16T09:00:00." + "123456"[:precision] + "+09:00"
    request = parse_request(encode(data))
    assert request.recorded_at == data["recordedAt"]
    assert request.model_dump(mode="json")["recordedAt"] == data["recordedAt"]


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-02-30T00:00:00Z",
        "2026-10-16T24:00:00Z",
        "2026-10-16T00:00:60Z",
        "2026-10-16T00:00:00+09:60",
        "2026-10-16T00:00:00+24:00",
        "2026-10-16T00:00:00.123456789Z",
        "2026-10-16T00:00Z",
        "2026-10-16T00:00:00",
        "2026-10-16 00:00:00Z",
        None,
        True,
    ],
)
def test_recorded_at_rejects_invalid_calendar_offset_and_precision(timestamp):
    data = request_data()
    data["recordedAt"] = timestamp
    with pytest.raises(AnalysisFailure) as caught:
        parse_request(encode(data))
    assert_failure(caught.value, 422, "INVALID_REQUEST")


@pytest.mark.parametrize(
    "raw_json",
    [
        b"{",
        b"[]",
        b"null",
        b"true",
        b'"text"',
        b"{} {}",
        b"\xff",
        b'{"content":NaN}',
        b'{"content":Infinity}',
        b'{"content":-Infinity}',
        b'{"content":"one","con\\u0074ent":"two"}',
    ],
)
def test_malformed_json_is_sanitized_at_both_boundaries(raw_json):
    with pytest.raises(AnalysisFailure) as caught:
        parse_request(raw_json)
    assert_failure(caught.value, 422, "INVALID_REQUEST")
    with pytest.raises(AnalysisFailure) as caught:
        validate_response(parse_request(raw("payloads/requests/base.json")), raw_json)
    assert_failure(caught.value, 502, "INVALID_AI_RESPONSE")


def test_response_duplicate_nested_keys_are_not_collapsed():
    provider_raw = raw("payloads/responses/update.json").replace(
        b'"scheduledDate": "2026-10-21"',
        b'"scheduledDate": "2026-10-20", "scheduledDate": "2026-10-21"',
    )
    with pytest.raises(AnalysisFailure) as caught:
        validate_response(parse_request(raw("payloads/requests/base.json")), provider_raw)
    assert_failure(caught.value, 502, "INVALID_AI_RESPONSE")


def test_source_text_and_opaque_direct_input_are_preserved():
    data = request_data()
    data["content"] = "  기록\n\tCafe\u0301  "
    basis = data["candidates"][0]["basis"][1]
    basis["text"] = ' { "b": 1, "a": 2, "a": 3 }\n'
    parsed = parse_request(encode(data))
    assert parsed.content == data["content"]
    assert parsed.candidates[0].basis[1].text == basis["text"]
    assert parsed.model_dump(mode="json") == data


def test_uuid_values_match_without_normalizing_text():
    data = request_data()
    data["requestId"] = data["requestId"].upper()
    data["candidates"][0]["itemId"] = data["candidates"][0]["itemId"].upper()
    data["candidates"][0]["basis"][0]["basisId"] = data["candidates"][0]["basis"][0][
        "basisId"
    ].upper()
    result = validate_response(parse_request(encode(data)), response_data())
    assert result.request_id == UUID(data["requestId"])


@pytest.mark.parametrize("location", ["candidates", "basis"])
def test_uuid_case_cannot_bypass_duplicate_input_ids(location):
    data = request_data()
    values = data["candidates"] if location == "candidates" else data["candidates"][0]["basis"]
    key = "itemId" if location == "candidates" else "basisId"
    duplicate = deepcopy(values[0])
    duplicate[key] = duplicate[key].upper()
    values.append(duplicate)
    with pytest.raises(AnalysisFailure) as caught:
        parse_request(encode(data))
    assert_failure(caught.value, 422, "INVALID_REQUEST")


@pytest.mark.parametrize("value", [True, 3.0, "3"])
def test_expected_version_requires_json_integer(value):
    provider = response_data()
    provider["suggestions"][0]["expectedVersion"] = value
    with pytest.raises(AnalysisFailure) as caught:
        validate_response(parse_request(raw("payloads/requests/base.json")), provider)
    assert_failure(caught.value, 502, "INVALID_AI_RESPONSE")


@pytest.mark.parametrize("candidate_index", range(5))
def test_every_current_value_key_is_required_even_when_nullable(candidate_index):
    data = request_data()
    original = data["candidates"][candidate_index]["currentValues"]
    for key in original:
        changed = deepcopy(data)
        del changed["candidates"][candidate_index]["currentValues"][key]
        with pytest.raises(AnalysisFailure) as caught:
            parse_request(encode(changed))
        assert_failure(caught.value, 422, "INVALID_REQUEST")


def test_v2_does_not_inherit_v1_length_or_count_limits():
    data = request_data()
    data["content"] += " 내용" * 4000
    data["candidates"][0]["basis"][0]["text"] = "근거" * 2001
    original = deepcopy(data["candidates"][0])
    for index in range(51):
        candidate = deepcopy(original)
        candidate["itemId"] = str(UUID(int=index + 1))
        for offset, basis in enumerate(candidate["basis"]):
            basis["basisId"] = str(UUID(int=1000 + 2 * index + offset))
        data["candidates"].append(candidate)
    parsed = parse_request(encode(data))
    assert len(parsed.candidates) == 57
    provider = response_data()
    provider["suggestions"][0]["title"] = "설명" * 201
    provider["suggestions"][0]["changes"]["description"] = "제안" * 2001
    provider["suggestions"] *= 31
    result = validate_response(parsed, provider)
    assert len(result.suggestions) == 31
    assert result.suggestions[0].title == provider["suggestions"][0]["title"]


def test_safe_errors_do_not_expose_input_or_provider_text(caplog):
    secret = "synthetic-sensitive-source-and-provider-text"
    data = request_data()
    data["candidates"][0]["type"] = secret
    provider = response_data()
    provider["suggestions"][0]["operation"] = secret
    for action in (
        lambda: parse_request(encode(data)),
        lambda: validate_response(parse_request(raw("payloads/requests/base.json")), provider),
    ):
        with pytest.raises(AnalysisFailure) as caught:
            action()
        assert secret not in str(caught.value)
        assert secret not in caught.value.message
        assert caught.value.__suppress_context__ is True
    assert secret not in caplog.text


def test_direct_input_reference_response_from_backend_fixture_is_valid():
    result = validate_response(
        parse_request(raw("payloads/requests/base.json")),
        raw("payloads/responses/update-direct-input.json"),
    )
    assert len(result.suggestions) == 1
    assert result.suggestions[0].basis_references[0].basis_ids == [
        UUID("aaaaaaaa-0000-4000-8000-000000000300")
    ]


def test_previously_validated_response_cannot_bypass_field_validation():
    request = parse_request(raw("payloads/requests/base.json"))
    result = validate_response(request, response_data())
    result.suggestions[0].changes["scheduledDate"] = "2026-02-30"
    with pytest.raises(AnalysisFailure) as caught:
        validate_response(request, result)
    assert_failure(caught.value, 502, "INVALID_AI_RESPONSE")


@pytest.mark.parametrize("collection", ["candidateItemIds", "basisIds", "referenceItems"])
def test_uuid_case_cannot_bypass_duplicate_response_ids(collection):
    provider = json.loads(raw("payloads/responses/ambiguous-two.json"))
    suggestion = provider["suggestions"][0]
    if collection == "candidateItemIds":
        suggestion[collection].append(suggestion[collection][0].upper())
    elif collection == "basisIds":
        identifiers = suggestion["basisReferences"][0]["basisIds"]
        identifiers.append(identifiers[0].upper())
    else:
        reference = deepcopy(suggestion["basisReferences"][0])
        reference["itemId"] = reference["itemId"].upper()
        suggestion["basisReferences"].append(reference)
    with pytest.raises(AnalysisFailure) as caught:
        validate_response(parse_request(raw("payloads/requests/base.json")), provider)
    assert_failure(caught.value, 502, "INVALID_AI_RESPONSE")
