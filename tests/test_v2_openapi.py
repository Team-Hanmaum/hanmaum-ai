"""Swagger examples, complete references and preservation of streamed validation."""

import json

import pytest
from fastapi import FastAPI
from test_v2_http import AUTH, Provider, build, client, raw, row_count

from app.api import router as v1_router
from app.v2.runtime import StubProvider
from app.v2.validation import parse_request, validate_response


@pytest.mark.anyio
async def test_openapi_describes_v2_without_changing_v1_and_all_references_resolve(tmp_path):
    app, _ = build(tmp_path, provider=Provider())
    async with client(app) as http:
        schema = (await http.get("/openapi.json")).json()
        assert (await http.get("/openapi.json")).json() == schema
    v1_app = FastAPI()
    v1_app.include_router(v1_router)
    baseline = v1_app.openapi()
    for path, value in baseline["paths"].items():
        assert schema["paths"][path] == value
    for name, value in baseline["components"]["schemas"].items():
        assert schema["components"]["schemas"][name] == value

    def resolve(reference):
        assert reference.startswith("#/")
        value = schema
        for part in reference[2:].split("/"):
            value = value[part.replace("~1", "/").replace("~0", "~")]
        return value

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert resolve(value["$ref"])
            if "discriminator" in value:
                for reference in value["discriminator"]["mapping"].values():
                    assert resolve(reference)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    operation = schema["paths"]["/v2/analyses"]["post"]
    assert "실제 LLM은 연결하지 않았" not in operation["description"]
    assert "openai" in operation["description"] and "stub" in operation["description"]
    provider_description = operation["responses"]["200"]["headers"]["X-AI-Provider"]["description"]
    assert "openai" in provider_description and "stub" in provider_description
    assert operation["security"] == [{"APIKeyHeader": []}]
    request_id = next(p for p in operation["parameters"] if p["name"] == "X-Request-Id")
    assert request_id["in"] == "header" and request_id["required"] is True
    assert request_id["schema"]["format"] == "uuid"
    assert operation["requestBody"]["required"] is True
    request_schema = resolve(
        operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    )
    assert set(request_schema["required"]) == {
        "requestId",
        "careSpaceId",
        "recordId",
        "content",
        "recordedAt",
        "timezone",
        "candidates",
    }
    assert request_schema["title"] == "V2AnalysisRequest"
    success = resolve(
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    )
    assert set(success["required"]) == {"requestId", "suggestions"}
    assert success["title"] == "V2AnalysisResponse"
    ready = schema["paths"]["/health/ready/v2"]["get"]
    assert ready["security"] == operation["security"]
    assert set(ready["responses"]) == {"200", "401", "503"}
    assert set(ready["responses"]["503"]["content"]["application/json"]["examples"]) == {
        "AI_NOT_CONFIGURED",
        "RECEIPT_STORE_UNAVAILABLE",
        "AI_OVERLOADED",
    }


@pytest.mark.anyio
async def test_swagger_error_examples_match_all_shared_http_code_pairs(tmp_path):
    app, _ = build(tmp_path)
    async with client(app) as http:
        schema = (await http.get("/openapi.json")).json()
    responses = schema["paths"]["/v2/analyses"]["post"]["responses"]
    examples = [
        (int(status), example["value"])
        for status, response in responses.items()
        if status != "200"
        for example in response["content"]["application/json"]["examples"].values()
    ]
    expected = json.loads(raw("error-mapping.json"))["mappings"]
    assert {(status, value["code"]) for status, value in examples} == {
        (entry["httpStatus"], entry["aiCode"]) for entry in expected
    }
    for _, value in examples:
        assert set(value) == {"code", "message"}
        assert value["message"].strip()


@pytest.mark.anyio
@pytest.mark.parametrize("example_name", ["minimal", "question"])
async def test_swagger_request_examples_execute_without_fastapi_body_buffering(
    example_name, tmp_path, monkeypatch
):
    app, instance = build(tmp_path, provider=StubProvider())

    async def forbidden_body(self):
        pytest.fail("Swagger documentation must not enable FastAPI's automatic body buffering")

    monkeypatch.setattr("starlette.requests.Request.body", forbidden_body)
    async with client(app) as http:
        schema = (await http.get("/openapi.json")).json()
        operation = schema["paths"]["/v2/analyses"]["post"]
        examples = operation["requestBody"]["content"]["application/json"]["examples"]
        value = examples[example_name]["value"]
        header_value = operation["parameters"][0]["example"]
        assert value["requestId"] == header_value
        headers = {"X-Internal-Api-Key": AUTH, "X-Request-Id": header_value}
        response = await http.post("/v2/analyses", json=value, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"requestId": header_value, "suggestions": []}
        assert row_count(instance) == 1
        duplicate = await http.post("/v2/analyses", json=value, headers=headers)
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "REQUEST_ALREADY_RECEIVED"

        response_examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
        for name, request_example in (("stub", "minimal"), ("resolve", "question")):
            request = parse_request(json.dumps(examples[request_example]["value"]))
            validate_response(request, response_examples[name]["value"])
