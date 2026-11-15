"""Network-free responses and settings for testing the OpenAI adapter."""

import json
from copy import deepcopy

import httpx

from app.v2.config import V2Settings
from tests.support.v2_fixtures import raw
from tests.support.v2_http import KEY

SECRET = "synthetic-openai-key-never-real"


def settings(tmp_path, **overrides):
    return V2Settings(
        _env_file=None,
        **(
            {
                "provider": "openai",
                "openai_api_key": SECRET,
                "receipt_db_path": tmp_path / "receipts.sqlite3",
                "hmac_keys": {"test": KEY},
                "active_hmac_key_version": "test",
            }
            | overrides
        ),
    )


def wire(body=None):
    data = deepcopy(
        body if body is not None else json.loads(raw("payloads/responses/resolve.json"))
    )
    data.pop("requestId")
    for suggestion in data["suggestions"]:
        suggestion["changes"] = [{"field": k, "value": v} for k, v in suggestion["changes"].items()]
    return data


def envelope(value=None):
    return {
        "status": "completed",
        "error": None,
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(value or wire())}],
            },
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 10},
            "output_tokens_details": {"reasoning_tokens": 20},
        },
    }


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def response(value=None, *, status=200, stream=None, extra_headers=None):
    return httpx.Response(
        status,
        headers={"content-type": "application/json"} | (extra_headers or {}),
        stream=stream or Stream([json.dumps(value or envelope()).encode()]),
    )
