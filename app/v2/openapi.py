"""Document streamed v2 requests without adding FastAPI body parsing or coercion."""

from copy import deepcopy

from fastapi import FastAPI

from app.v2.schemas import AnalysisRequest, AnalysisResponse

EXAMPLE_REQUEST_ID = "aaaaaaaa-0000-4000-8000-000000000001"
EXAMPLE_REQUEST = {
    "requestId": EXAMPLE_REQUEST_ID,
    "careSpaceId": "aaaaaaaa-0000-4000-8000-000000000002",
    "recordId": "aaaaaaaa-0000-4000-8000-000000000003",
    "content": "다음 방문에는 수건을 준비해 주세요.",
    "recordedAt": "2026-10-21T09:00:00+09:00",
    "timezone": "Asia/Seoul",
    "candidates": [],
}
QUESTION_REQUEST = EXAMPLE_REQUEST | {
    "candidates": [
        {
            "itemId": "aaaaaaaa-0000-4000-8000-000000000100",
            "version": 1,
            "type": "QUESTION",
            "currentValues": {
                "title": "방문 준비물",
                "description": None,
                "status": "UNCONFIRMED",
                "answerText": None,
            },
            "basis": [
                {
                    "basisId": "aaaaaaaa-0000-4000-8000-000000000200",
                    "kind": "SOURCE_RECORD",
                    "text": "방문 전에 무엇을 준비해야 하나요?",
                }
            ],
        }
    ],
}
EXAMPLE_RESPONSE = {"requestId": EXAMPLE_REQUEST_ID, "suggestions": []}
QUESTION_RESPONSE = {
    "requestId": EXAMPLE_REQUEST_ID,
    "suggestions": [
        {
            "operation": "RESOLVE",
            "type": "QUESTION",
            "targetItemId": "aaaaaaaa-0000-4000-8000-000000000100",
            "expectedVersion": 1,
            "candidateItemIds": [],
            "title": "방문 준비물 답변",
            "evidence": "수건을 준비해 주세요.",
            "changes": {"answerText": "수건을 준비하면 됩니다."},
            "clearFields": [],
            "relationKind": "RELATED",
            "reviewReasons": [],
            "basisReferences": [
                {
                    "itemId": "aaaaaaaa-0000-4000-8000-000000000100",
                    "basisIds": ["aaaaaaaa-0000-4000-8000-000000000200"],
                }
            ],
        }
    ],
}

ERRORS = {
    400: {"REQUEST_ID_MISMATCH": "헤더와 본문의 요청 ID가 다릅니다."},
    401: {"UNAUTHORIZED": "내부 인증이 필요합니다."},
    409: {
        "REQUEST_ALREADY_RECEIVED": "이미 접수한 요청입니다.",
        "REQUEST_ID_REUSED": "다른 본문에 사용된 요청 ID입니다.",
    },
    413: {"REQUEST_TOO_LARGE": "요청 본문 크기가 제한을 초과했습니다."},
    422: {"INVALID_REQUEST": "요청 형식과 필수 항목을 확인해주세요."},
    502: {
        "INVALID_AI_RESPONSE": "분석 결과를 검증하지 못했습니다.",
        "AI_UNAVAILABLE": "분석 서비스를 사용할 수 없습니다.",
    },
    503: {
        "AI_NOT_CONFIGURED": "분석 제공자가 설정되지 않았습니다.",
        "RECEIPT_STORE_UNAVAILABLE": "수신 기록 저장소를 사용할 수 없습니다.",
        "REQUEST_COMPARISON_UNAVAILABLE": "수신 기록을 안전하게 비교할 수 없습니다.",
        "AI_OVERLOADED": "분석 처리 용량이 부족합니다.",
    },
    504: {"AI_TIMEOUT": "분석 응답 대기 시간이 초과되었습니다."},
}


def error_responses(errors):
    return {
        status: {
            "description": " / ".join(codes),
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/V2ErrorResponse"},
                    "examples": {
                        code: {"summary": message, "value": {"code": code, "message": message}}
                        for code, message in codes.items()
                    },
                }
            },
        }
        for status, codes in errors.items()
    }


ANALYSIS_OPENAPI = {
    "parameters": [
        {
            "name": "X-Request-Id",
            "in": "header",
            "required": True,
            "description": (
                "본문 requestId와 같은 UUID. 새 실행은 두 값을 함께 새 UUID로 변경합니다."
            ),
            "schema": {"type": "string", "format": "uuid"},
            "example": EXAMPLE_REQUEST_ID,
        }
    ],
    "requestBody": {
        "required": True,
        "description": (
            "비압축 UTF-8 JSON 원문은 1,048,576바이트 이하입니다. "
            "문자열은 디코딩 후 타입 변환 전에 UTF-16 코드 단위로 계산합니다. "
            "content 10,000, 후보 100개, 후보별 근거 20개·전체 500개, "
            "근거 text 20,000, currentValues 자유 서술 필드 각각 10,000, "
            "전체 문자열 합계 100,000 이하를 동시에 만족해야 합니다."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/V2AnalysisRequest"},
                "examples": {
                    "minimal": {"summary": "후보 없는 최소 요청", "value": EXAMPLE_REQUEST},
                    "question": {
                        "summary": "질문 후보와 원본 근거가 있는 요청",
                        "value": QUESTION_REQUEST,
                    },
                },
            }
        },
    },
}

ANALYSIS_RESPONSES = {
    200: {
        "description": (
            "검증된 전체 제안. 로컬 stub은 빈 suggestions를 반환합니다. "
            "제공자 원문·최종 응답은 524,288바이트, 제안 30개, 표시 title 500, evidence 10,000, "
            "changes 자유 서술 필드 각각 10,000, 전체 문자열 합계 50,000 이하입니다. "
            "문자열 단위는 UTF-16입니다. 제안별 candidateItemIds·basisReferences는 각각 100개, "
            "참조 항목별 basisIds는 20개·제안별 합계 500개 이하입니다."
        ),
        "headers": {
            "X-Request-Id": {"schema": {"type": "string", "format": "uuid"}},
            "X-AI-Provider": {
                "schema": {"type": "string"},
                "description": "현재 로컬 제공자는 stub",
            },
            "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
        },
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/V2AnalysisResponse"},
                "examples": {
                    "stub": {"summary": "로컬 stub의 빈 제안", "value": EXAMPLE_RESPONSE},
                    "resolve": {
                        "summary": "질문 답변 제안의 계약 예시 (stub이 생성하는 결과가 아님)",
                        "value": QUESTION_RESPONSE,
                    },
                },
            }
        },
    },
    **error_responses(ERRORS),
}

READINESS_RESPONSES = {
    200: {
        "description": "v2 제공자 설정과 수신 저장소 접근·스키마 확인 완료",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/V2Readiness"},
                "example": {"status": "UP", "provider": "stub"},
            }
        },
    },
    **error_responses(
        {
            401: ERRORS[401],
            503: {
                code: ERRORS[503][code]
                for code in ("AI_NOT_CONFIGURED", "RECEIPT_STORE_UNAVAILABLE")
            },
        }
    ),
}


def install_openapi(app: FastAPI) -> None:
    """Register namespaced v2 components, leaving v1 names and runtime parsing intact."""
    original_openapi = app.openapi

    def openapi():
        if app.openapi_schema is None:
            schema = original_openapi()
            # FastAPI excludes None while building OpenAPI, including example values.
            # Required nullable v2 fields must remain explicit JSON null in Swagger.
            operation = schema["paths"]["/v2/analyses"]["post"]
            operation["requestBody"] = deepcopy(ANALYSIS_OPENAPI["requestBody"])
            operation["responses"]["200"] = deepcopy(ANALYSIS_RESPONSES[200])
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            for model, mode in (
                (AnalysisRequest, "validation"),
                (AnalysisResponse, "serialization"),
            ):
                document = model.model_json_schema(
                    by_alias=True, mode=mode, ref_template="#/components/schemas/V2{model}"
                )
                for name, definition in document.pop("$defs", {}).items():
                    definition["title"] = f"V2{name}"
                    components[f"V2{name}"] = definition
                document["title"] = f"V2{model.__name__}"
                components[f"V2{model.__name__}"] = document
            components["V2ErrorResponse"] = {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "message"],
                "properties": {
                    "code": {
                        "type": "string",
                        "enum": [code for codes in ERRORS.values() for code in codes],
                    },
                    "message": {"type": "string", "description": "민감한 내용을 제외한 안내 문구"},
                },
            }
            components["V2Readiness"] = {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "provider"],
                "properties": {
                    "status": {"type": "string", "const": "UP"},
                    "provider": {"type": "string"},
                },
            }
        return app.openapi_schema

    app.openapi = openapi
