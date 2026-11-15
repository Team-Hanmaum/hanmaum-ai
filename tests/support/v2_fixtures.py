"""Shared fixture paths and byte-preserving readers."""

import json
from functools import lru_cache
from pathlib import Path

from scripts.fixture_payloads import materialize

ROOT = Path(__file__).parents[1] / "fixtures/v2"
GENERATED = json.loads((ROOT / "generated.json").read_bytes())


def raw(path="payloads/requests/base.json"):
    return (ROOT / path).read_bytes()


@lru_cache(maxsize=128)
def generated(name):
    return materialize(ROOT, GENERATED["recipes"], name)


def body(spec):
    return raw(spec["bodyFile"]) if "bodyFile" in spec else generated(spec["bodyRecipe"])
