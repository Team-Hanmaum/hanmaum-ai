import json
from pathlib import Path

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for name in ("APP_ENV", "AI_PROVIDER", "AI_INTERNAL_API_KEY", "ANALYSIS_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)


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
