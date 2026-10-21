"""v2-20261021.1 limits, applied to decoded values before model conversion."""

import json

REQUEST_BYTES = 1_048_576
RESPONSE_BYTES = 524_288
FREE_TEXT_FIELDS = {
    "title",
    "description",
    "location",
    "observedTimeText",
    "sourceName",
    "answerText",
}


class RequestTooLarge(ValueError):
    pass


def utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def check_raw_size(raw: bytes | str, maximum: int) -> None:
    if len(raw) > maximum or (isinstance(raw, str) and len(raw.encode("utf-8")) > maximum):
        raise RequestTooLarge("JSON body exceeds byte limit")


def encode_json(value: object, maximum: int = RESPONSE_BYTES) -> bytes:
    """Bound accumulation, including keys/escapes/whitespace in the outgoing body."""
    result = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        if len(chunk) > maximum - len(result):
            raise ValueError("Encoded JSON exceeds byte limit")
        encoded = chunk.encode("utf-8")
        if len(encoded) > maximum - len(result):
            raise ValueError("Encoded JSON exceeds byte limit")
        result.extend(encoded)
    return bytes(result)


def _text(value, maximum):
    if isinstance(value, str) and utf16_units(value) > maximum:
        raise ValueError("String exceeds UTF-16 limit")


def _array(value, maximum):
    if isinstance(value, list):
        if len(value) > maximum:
            raise ValueError("Collection exceeds limit")
        return value
    return ()  # Structural/type validation belongs to the strict wire models.


def _fields(value, maximum):
    if isinstance(value, dict):
        for key in FREE_TEXT_FIELDS:
            _text(value.get(key), maximum)


def check_string_total(value, maximum):
    # Iterate without creating another flattened collection; keys are not values.
    stack = [iter((value,))]
    total = 0
    while stack:
        try:
            child = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if isinstance(child, str):
            total += utf16_units(child)
            if total > maximum:
                raise ValueError("String total exceeds UTF-16 limit")
        elif isinstance(child, dict):
            stack.append(iter(child.values()))
        elif isinstance(child, list):
            stack.append(iter(child))


def check_request(value):
    check_string_total(value, 100_000)
    if not isinstance(value, dict):
        return
    _text(value.get("content"), 10_000)
    basis_total = 0
    for candidate in _array(value.get("candidates"), 100):
        if not isinstance(candidate, dict):
            continue
        _fields(candidate.get("currentValues"), 10_000)
        basis = _array(candidate.get("basis"), 20)
        basis_total += len(basis)
        if basis_total > 500:
            raise ValueError("Total basis exceeds limit")
        for item in basis:
            if isinstance(item, dict):
                _text(item.get("text"), 20_000)


def check_response(value):
    check_string_total(value, 50_000)
    if not isinstance(value, dict):
        return
    for suggestion in _array(value.get("suggestions"), 30):
        if not isinstance(suggestion, dict):
            continue
        _text(suggestion.get("title"), 500)
        _text(suggestion.get("evidence"), 10_000)
        _fields(suggestion.get("changes"), 10_000)
        _array(suggestion.get("candidateItemIds"), 100)
        basis_total = 0
        for reference in _array(suggestion.get("basisReferences"), 100):
            if isinstance(reference, dict):
                basis_total += len(_array(reference.get("basisIds"), 20))
        if basis_total > 500:
            raise ValueError("Total referenced basis exceeds limit")
