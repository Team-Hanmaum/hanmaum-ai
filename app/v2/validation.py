"""Safe contract entry points; no authentication, provider calls or BE state access."""

import json

from app.errors import AnalysisFailure
from app.v2.limits import (
    REQUEST_BYTES,
    RESPONSE_BYTES,
    RequestTooLarge,
    check_raw_size,
    check_request,
    check_response,
    encode_json,
)
from app.v2.schemas import AnalysisRequest, AnalysisResponse


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("Non-JSON number")


def load_json(raw: bytes | str, maximum: int = REQUEST_BYTES) -> object:
    """Bound buffered input before decoding; HTTP also bounds the stream while reading."""
    check_raw_size(raw, maximum)
    # Decode explicitly: json.loads(bytes) also accepts UTF-16/32, unlike this wire contract.
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise ValueError("Expected UTF-8 JSON")
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def parse_request(raw: bytes | str) -> AnalysisRequest:
    """Parse original JSON into a deeply immutable response-validation snapshot."""
    try:
        value = load_json(raw)
        check_request(value)
        return AnalysisRequest.model_validate(value)
    except RequestTooLarge:
        raise AnalysisFailure(
            413, "REQUEST_TOO_LARGE", "요청 본문 크기가 제한을 초과했습니다."
        ) from None
    except (ValueError, TypeError, RecursionError):
        raise AnalysisFailure(
            422, "INVALID_REQUEST", "요청 형식과 필수 항목을 확인해주세요."
        ) from None


def validate_response(request: AnalysisRequest, payload: object) -> AnalysisResponse:
    """Validate provider JSON or an AnalysisResponse, then all request-relative rules.

    Raw bytes/text retain duplicate keys for rejection. An already decoded provider object
    cannot recover duplicates; its adapter must reject duplicates when decoding raw output.
    Keep the original parse_request snapshot for this check. Providers needing mutable
    input may receive request.model_dump(mode="json"), not a replacement validation basis.
    No partial list is returned if any suggestion fails.
    """
    try:
        if isinstance(payload, AnalysisResponse):
            # Revalidate nested fields as well: model instances may have been mutated.
            payload = payload.model_dump(mode="json", by_alias=True, warnings="error")
        if isinstance(payload, (bytes, str)):
            payload = load_json(payload, RESPONSE_BYTES)
        else:
            # Decoded adapters must bound their own reads; also bound the object at this boundary.
            encode_json(payload)
        check_response(payload)
        response = AnalysisResponse.model_validate(payload)
        validate_references(request, response)
        return response
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise AnalysisFailure(
            502, "INVALID_AI_RESPONSE", "분석 결과를 검증하지 못했습니다."
        ) from None


def serialize_response(response: AnalysisResponse) -> bytes:
    try:
        value = response.model_dump(mode="json", by_alias=True)
        check_response(value)
        return encode_json(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise AnalysisFailure(
            502, "INVALID_AI_RESPONSE", "분석 결과를 검증하지 못했습니다."
        ) from None


def validate_references(request: AnalysisRequest, response: AnalysisResponse) -> None:
    if response.request_id != request.request_id:
        raise ValueError("Response request ID does not match")
    candidates = {candidate.item_id: candidate for candidate in request.candidates}
    for suggestion in response.suggestions:
        if suggestion.evidence not in request.content:
            raise ValueError("Evidence must occur in the source content")
        if suggestion.target_item_id is not None:
            target = candidates.get(suggestion.target_item_id)
            if (
                target is None
                or target.type != suggestion.type
                or target.version != suggestion.expected_version
            ):
                raise ValueError("Target type and version must match the input candidate")
        for item_id in suggestion.candidate_item_ids:
            candidate = candidates.get(item_id)
            if candidate is None or candidate.type != suggestion.type:
                raise ValueError("Ambiguous candidates must exist and match the item type")

        references = {ref.item_id: ref.basis_ids for ref in suggestion.basis_references}
        for item_id, basis_ids in references.items():
            candidate = candidates.get(item_id)
            if candidate is None:
                raise ValueError("Referenced candidate does not exist")
            if not set(basis_ids) <= {basis.basis_id for basis in candidate.basis}:
                raise ValueError("Referenced basis does not belong to the input candidate")
        required = set(suggestion.candidate_item_ids)
        if suggestion.target_item_id is not None:
            required.add(suggestion.target_item_id)
        if not required <= references.keys():
            raise ValueError("Every target candidate requires at least one comparison basis")
