"""Safe contract entry points; no authentication, provider calls or BE state access."""

import json

from app.errors import AnalysisFailure
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


def load_json(raw: bytes | str) -> object:
    # Decode explicitly: json.loads(bytes) also accepts UTF-16/32, unlike this wire contract.
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise ValueError("Expected UTF-8 JSON")
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def parse_request(raw: bytes | str) -> AnalysisRequest:
    """Parse original JSON before duplicate keys or timestamp precision can be lost."""
    try:
        return AnalysisRequest.model_validate(load_json(raw))
    except (ValueError, RecursionError):
        raise AnalysisFailure(
            422, "INVALID_REQUEST", "요청 형식과 필수 항목을 확인해주세요."
        ) from None


def validate_response(request: AnalysisRequest, payload: object) -> AnalysisResponse:
    """Validate a provider JSON object or original JSON, then all request-relative rules.

    Raw bytes/text retain duplicate keys for rejection. An already decoded provider object
    cannot recover duplicates; its adapter must reject duplicates when decoding raw output.
    No partial list is returned if any suggestion fails.
    """
    try:
        if isinstance(payload, (bytes, str)):
            payload = load_json(payload)
        response = AnalysisResponse.model_validate(payload)
        validate_references(request, response)
        return response
    except (ValueError, RecursionError):
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
