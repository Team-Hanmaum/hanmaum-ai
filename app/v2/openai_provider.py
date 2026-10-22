"""Async Responses API adapter: no retries, redirects, tools, persistence or raw error logs."""

import asyncio
import json
import logging
from dataclasses import dataclass
from time import perf_counter

import httpx

from app.v2.config import V2Settings
from app.v2.diagnostics import FailureEvent, record_failure
from app.v2.limits import encode_json
from app.v2.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from app.v2.provider_errors import InvalidProviderOutput
from app.v2.structured_output import output_schema, translate_output
from app.v2.validation import load_json

ENDPOINT = "https://api.openai.com/v1/responses"
# Upstream envelope includes JSON-escaped model text, usage and reasoning metadata.
# This is an AI transport guard, not a change to BE's 524288-byte response limit.
ENVELOPE_BYTES = 2_097_152
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Observation:
    outcome: str
    elapsed_seconds: float
    http_status: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    call_number: int | None = None


def token_count(value):
    return value if type(value) is int and value >= 0 else None


class OpenAIProvider:
    def __init__(self, settings: V2Settings, *, transport=None):
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._closed = False
        self.calls = 0
        # One bounded diagnostic sample, not a request/result history. Evals run sequentially.
        self.last_observation: Observation | None = None

    def _get_client(self):
        if self._closed:
            raise RuntimeError("Provider closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(self.settings.provider_timeout_seconds, connect=3, pool=1),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    async def close(self):
        self._closed = True
        if self._client is not None:
            await self._client.aclose()

    def request_body(self, request):
        # Server-only identity is unnecessary for generation; candidate/basis IDs remain
        # necessary for the unchanged reference contract and server-side validation.
        model_input = request.model_dump(
            mode="json", by_alias=True, include={"content", "recorded_at", "timezone", "candidates"}
        )
        return {
            "model": self.settings.model,
            "reasoning": {"effort": self.settings.reasoning_effort},
            "service_tier": self.settings.service_tier,
            "max_output_tokens": self.settings.max_output_tokens,
            "store": False,
            "stream": False,
            "truncation": "disabled",
            "tools": [],
            "instructions": SYSTEM_PROMPT,
            "input": [
                {
                    "role": "user",
                    # Readable Korean avoids making the model interpret six-character escapes.
                    # Escape only lone surrogates; no decoded input value is changed.
                    "content": json.dumps(model_input, ensure_ascii=False)
                    .encode("utf-8", errors="backslashreplace")
                    .decode("utf-8"),
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "hanmaum_v2_analysis",
                    "strict": True,
                    "schema": output_schema(),
                }
            },
        }

    async def analyze(self, request):
        started = perf_counter()
        outcome, status, usage = "unavailable", None, {}
        call_number = None
        try:
            # ASCII encoding also preserves escaped lone surrogates accepted by the wire contract.
            body = encode_json(self.request_body(request), maximum=4 * 1_048_576)
            key = self.settings.openai_api_key.get_secret_value()
            self.calls += 1
            call_number = self.calls
            async with self._get_client().stream(
                "POST",
                ENDPOINT,
                content=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                    "Accept": "application/json",
                },
            ) as response:
                status = response.status_code
                if status != 200:
                    event = {
                        401: FailureEvent.OPENAI_AUTH,
                        403: FailureEvent.OPENAI_AUTH,
                        404: FailureEvent.OPENAI_NOT_FOUND,
                        429: FailureEvent.OPENAI_RATE_LIMIT,
                    }.get(status, FailureEvent.OPENAI_HTTP)
                    record_failure(event)
                    # Do not read even a single error-body chunk or follow a credential redirect.
                    raise RuntimeError("OpenAI request failed")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise InvalidProviderOutput()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type != "application/json":
                    raise InvalidProviderOutput()
                length = response.headers.get("content-length")
                if length is not None:
                    digits = length.lstrip("0") or "0"
                    if not length.isascii() or not length.isdigit():
                        raise InvalidProviderOutput()
                    if len(digits) > 7 or int(digits) > ENVELOPE_BYTES:
                        record_failure(FailureEvent.OPENAI_SIZE)
                        raise InvalidProviderOutput()
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    if len(chunk) > ENVELOPE_BYTES - len(raw):
                        record_failure(FailureEvent.OPENAI_SIZE)
                        raise InvalidProviderOutput()
                    raw.extend(chunk)
            try:
                envelope = load_json(bytes(raw), ENVELOPE_BYTES)
                if not isinstance(envelope, dict):
                    raise ValueError("Invalid envelope")
                usage = envelope.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                error = envelope.get("error")
                if error is not None:
                    if not isinstance(error, dict) or not all(
                        isinstance(error.get(field), str) and error[field]
                        for field in ("code", "message")
                    ):
                        raise ValueError("Invalid error envelope")
                    record_failure(FailureEvent.OPENAI_RESPONSE_ERROR)
                    raise RuntimeError("OpenAI response failed")
                if envelope.get("status") != "completed":
                    record_failure(FailureEvent.OPENAI_INCOMPLETE)
                    raise InvalidProviderOutput()
                output = envelope.get("output")
                if not isinstance(output, list):
                    raise ValueError("Invalid output")
                messages = []
                for item in output:
                    if not isinstance(item, dict):
                        raise ValueError("Invalid item")
                    if item.get("type") == "reasoning":
                        continue
                    if (item.get("type"), item.get("role"), item.get("status")) != (
                        "message",
                        "assistant",
                        "completed",
                    ):
                        raise ValueError("Unexpected output item")
                    messages.append(item)
                if len(messages) != 1 or not isinstance(messages[0].get("content"), list):
                    raise ValueError("Expected one assistant message")
                content = messages[0]["content"]
                if any(
                    isinstance(part, dict) and part.get("type") == "refusal" for part in content
                ):
                    record_failure(FailureEvent.OPENAI_REFUSAL)
                    raise InvalidProviderOutput()
                if len(content) != 1 or not isinstance(content[0], dict):
                    raise ValueError("Expected one text part")
                if content[0].get("type") != "output_text" or not isinstance(
                    content[0].get("text"), str
                ):
                    raise ValueError("Expected output text")
                result = translate_output(content[0]["text"], str(request.request_id))
            except (ValueError, TypeError, RecursionError, OverflowError):
                raise InvalidProviderOutput() from None
            outcome = "returned"  # Contract/semantic validity is still checked by the service.
            return result
        except InvalidProviderOutput:
            outcome = "invalid_output"
            record_failure(FailureEvent.OPENAI_INVALID)
            raise InvalidProviderOutput() from None
        except httpx.TimeoutException:
            outcome = "timeout"
            raise TimeoutError() from None
        except httpx.HTTPError:
            record_failure(FailureEvent.OPENAI_NETWORK)
            raise RuntimeError("OpenAI transport failed") from None
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            input_details = usage.get("input_tokens_details")
            output_details = usage.get("output_tokens_details")
            self.last_observation = Observation(
                outcome,
                round(perf_counter() - started, 6),
                status,
                token_count(usage.get("input_tokens")),
                token_count(usage.get("output_tokens")),
                token_count(input_details.get("cached_tokens"))
                if isinstance(input_details, dict)
                else None,
                token_count(output_details.get("reasoning_tokens"))
                if isinstance(output_details, dict)
                else None,
                call_number=call_number,
            )
            _logger.info("OpenAI call prompt=%s metrics=%s", PROMPT_VERSION, self.last_observation)
