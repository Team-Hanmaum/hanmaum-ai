"""Versioned, private request fingerprints; no HTTP policy or receipt persistence."""

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID

from pydantic import SecretBytes

from app.v2.schemas import AnalysisRequest

CANONICALIZATION_VERSION = "v2-json-1"


class ComparisonUnavailable(Exception):
    """Fail closed internally. The external status/code still requires BE agreement."""

    def __init__(self):
        super().__init__("수신 기록을 안전하게 비교할 수 없습니다.")


def canonicalize_v2(request: AnalysisRequest) -> bytes:
    def normalize(value: object) -> object:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    body = request.model_dump(mode="python", by_alias=True)
    timestamp = datetime.fromisoformat(request.recorded_at).astimezone(UTC)
    body["recordedAt"] = timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
    # ASCII escaping preserves decoded strings (including lone JSON surrogates).
    # Only typed UUID objects and recordedAt are normalized, never text contents.
    return json.dumps(
        normalize(body), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True)
class Fingerprint:
    canonicalization_version: str
    hmac_key_version: str
    digest: bytes = field(repr=False)


class RequestDigester:
    def __init__(
        self,
        *,
        keys: Mapping[str, SecretBytes],
        active_key_version: str,
        active_rule_version: str = CANONICALIZATION_VERSION,
        rules: Mapping[str, Callable[[AnalysisRequest], bytes]] | None = None,
    ):
        rules = dict(rules) if rules is not None else {CANONICALIZATION_VERSION: canonicalize_v2}
        if (
            active_key_version not in keys
            or active_rule_version not in rules
            or any(not version for version in (*keys, *rules))
            or any(
                not isinstance(key, SecretBytes) or len(key.get_secret_value()) < 32
                for key in keys.values()
            )
        ):
            raise ValueError(
                "Active comparison rule and dedicated HMAC keys of at least 32 bytes required"
            )
        # Configuration snapshots cannot change during an in-flight comparison.
        self._keys = MappingProxyType(dict(keys))
        self._rules = MappingProxyType(rules)
        self._active_key_version = active_key_version
        self._active_rule_version = active_rule_version

    def fingerprint(
        self,
        request: AnalysisRequest,
        *,
        rule_version: str | None = None,
        key_version: str | None = None,
    ) -> Fingerprint:
        rule_version = self._active_rule_version if rule_version is None else rule_version
        key_version = self._active_key_version if key_version is None else key_version
        try:
            rule, key = self._rules[rule_version], self._keys[key_version]
            context = json.dumps(
                ["hanmaum-ai-receipt", "v2", rule_version, key_version], separators=(",", ":")
            ).encode("utf-8")
            digest = hmac.new(key.get_secret_value(), digestmod=hashlib.sha256)
            digest.update(context + b"\0")
            digest.update(rule(request))
            return Fingerprint(rule_version, key_version, digest.digest())
        except Exception:
            # Neither a missing historic rule/key nor a failing rule permits a new call.
            raise ComparisonUnavailable() from None

    def matches(self, request: AnalysisRequest, stored: Fingerprint) -> bool:
        if (
            not isinstance(stored.canonicalization_version, str)
            or not stored.canonicalization_version
            or not isinstance(stored.hmac_key_version, str)
            or not stored.hmac_key_version
            or not isinstance(stored.digest, bytes)
            or len(stored.digest) != hashlib.sha256().digest_size
        ):
            raise ComparisonUnavailable()
        candidate = self.fingerprint(
            request,
            rule_version=stored.canonicalization_version,
            key_version=stored.hmac_key_version,
        )
        return hmac.compare_digest(candidate.digest, stored.digest)
