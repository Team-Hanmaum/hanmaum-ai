"""Strict OpenAI shape and lossless translation to the unchanged BE changes object."""

from app.v2.limits import RESPONSE_BYTES
from app.v2.schemas import CHANGE_FIELDS, AnalysisResponse
from app.v2.validation import load_json


def output_schema() -> dict:
    schema = AnalysisResponse.model_json_schema(by_alias=True)
    # Request identity belongs to the server, not to model generation.
    del schema["properties"]["requestId"]
    schema["required"] = ["suggestions"]
    schema["properties"]["suggestions"]["maxItems"] = 30
    fields = sorted(set().union(*CHANGE_FIELDS.values()) | {"answerText"})
    schema["$defs"]["Suggestion"]["properties"]["changes"] = {
        "type": "array",
        "maxItems": len(fields),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["field", "value"],
            "properties": {
                "field": {"type": "string", "enum": fields},
                "value": {"type": "string"},
            },
        },
    }
    return schema


def translate_output(text: str, request_id: str) -> dict:
    value = load_json(text, RESPONSE_BYTES)
    if not isinstance(value, dict) or set(value) != {"suggestions"}:
        raise ValueError("Invalid output root")
    suggestions = value["suggestions"]
    if not isinstance(suggestions, list) or len(suggestions) > 30:
        raise ValueError("Invalid suggestions")
    for suggestion in suggestions:
        if not isinstance(suggestion, dict) or not isinstance(suggestion.get("changes"), list):
            raise ValueError("Invalid changes")
        changes = {}
        for entry in suggestion["changes"]:
            if not isinstance(entry, dict) or set(entry) != {"field", "value"}:
                raise ValueError("Invalid change entry")
            field = entry["field"]
            if not isinstance(field, str) or field in changes:
                raise ValueError("Invalid or repeated change field")
            changes[field] = entry["value"]
        suggestion["changes"] = changes
    # No stripping, coercion, partial filtering, default values or repair call.
    # The service validates the entire translated object, including original references.
    return {"requestId": request_id, "suggestions": suggestions}
