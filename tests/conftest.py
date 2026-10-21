import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for name in ("APP_ENV", "AI_PROVIDER", "AI_INTERNAL_API_KEY", "ANALYSIS_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    for name in tuple(os.environ):
        if name.startswith("AI_V2_"):
            monkeypatch.delenv(name, raising=False)
    # v1 tests must remain independent of a developer's optional local v2 configuration.
    monkeypatch.setenv("AI_V2_PROVIDER", "disabled")
    monkeypatch.setenv("AI_V2_HMAC_KEYS", "{}")


@pytest.fixture
def analysis_request():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "analysis-request.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def analysis_response():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "analysis-response.json").read_text(encoding="utf-8")
    )
