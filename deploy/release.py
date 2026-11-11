"""Validate release metadata and raw Compose env files without evaluating shell code."""

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

REPOSITORY = "Team-Hanmaum/hanmaum-ai"
IMAGE = r"ghcr\.io/team-hanmaum/hanmaum-ai@sha256:[0-9a-f]{64}"
RELEASE_ID = r"[0-9a-f]{40}-[0-9]+-[0-9]+"
STABLE = ("AI_INTERNAL_API_KEY", "AI_V2_HMAC_KEYS", "AI_V2_ACTIVE_HMAC_KEY_VERSION")
DEFAULTS = {
    "APP_ENV": "prod",
    "AI_PROVIDER": "disabled",
    "AI_V2_PROVIDER": "openai",
    "AI_V2_RECEIPT_DB_PATH": "/var/lib/hanmaum-ai/receipts.sqlite3",
    "AI_V2_MODEL": "gpt-6-luna",
    "AI_V2_REASONING_EFFORT": "low",
    "AI_V2_SERVICE_TIER": "default",
    "AI_V2_MAX_OUTPUT_TOKENS": "4096",
}
CONFIGURABLE = ("AI_V2_MODEL", "AI_V2_REASONING_EFFORT", "AI_V2_MAX_OUTPUT_TOKENS")


def runtime_values(environment):
    values = dict(DEFAULTS)
    for key in (*STABLE, "OPENAI_API_KEY", *CONFIGURABLE):
        value = environment.get(key) or values.get(key, "")
        if not isinstance(value, str) or not value or any(c in value for c in "\r\n\0"):
            raise ValueError(f"{key} must be a nonempty single-line value")
        values[key] = value
    for key in ("AI_INTERNAL_API_KEY", "OPENAI_API_KEY"):
        value = values[key]
        if not value.isascii() or any(c.isspace() for c in value):
            raise ValueError(f"{key} must contain only non-whitespace ASCII")
    if len(values["AI_INTERNAL_API_KEY"]) < 32:
        raise ValueError("AI_INTERNAL_API_KEY must have at least 32 characters")
    if values["AI_INTERNAL_API_KEY"] == values["OPENAI_API_KEY"]:
        raise ValueError("Internal and provider keys must be separate")
    try:
        keys = json.loads(values["AI_V2_HMAC_KEYS"])
    except (ValueError, TypeError):
        raise ValueError("AI_V2_HMAC_KEYS must be a JSON key map") from None
    if (
        not isinstance(keys, dict)
        or values["AI_V2_ACTIVE_HMAC_KEY_VERSION"] not in keys
        or any(
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", version)
            or not isinstance(secret, str)
            or len(secret.encode()) < 32
            or secret in (values["AI_INTERNAL_API_KEY"], values["OPENAI_API_KEY"])
            for version, secret in keys.items()
        )
    ):
        raise ValueError("Invalid or reused receipt HMAC key")
    values["AI_V2_HMAC_KEYS"] = json.dumps(keys, sort_keys=True, ensure_ascii=True)
    if not re.fullmatch(r"[a-zA-Z0-9._-]{1,128}", values["AI_V2_MODEL"]):
        raise ValueError("Invalid model name")
    if values["AI_V2_REASONING_EFFORT"] not in ("none", "low", "medium", "high", "xhigh", "max"):
        raise ValueError("Invalid reasoning effort")
    if not re.fullmatch(r"[0-9]{3,5}", values["AI_V2_MAX_OUTPUT_TOKENS"]) or not (
        256 <= int(values["AI_V2_MAX_OUTPUT_TOKENS"]) <= 32768
    ):
        raise ValueError("Invalid output token budget")
    return values


def write_private(path, text):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        os.chmod(path, 0o600)
        stream.write(text)


def read_runtime(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError("Malformed runtime environment file")
        values[key] = value
    if set(values) != set(DEFAULTS) | set(STABLE) | {"OPENAI_API_KEY"}:
        raise ValueError("Unexpected runtime environment keys")
    checked = runtime_values(values)
    if values != checked:
        raise ValueError("Runtime file must use the validated production configuration")
    return values


def compare_stable(previous, candidate):
    if any(previous[key] != candidate[key] for key in STABLE):
        raise ValueError("Internal/HMAC keys or active version changed; planned rotation required")


def metadata(environment):
    value = {
        "sha": environment["RELEASE_SHA"],
        "id": f"{environment['RELEASE_SHA']}-{environment['GITHUB_RUN_ID']}"
        f"-{environment['GITHUB_RUN_ATTEMPT']}",
        "image": environment["AI_IMAGE"],
        "memory": environment["AI_MEMORY_LIMIT"],
        "actor": environment["GITHUB_ACTOR"],
        "initialize": environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and environment.get("INITIALIZE_AI") == "true",
    }
    validate_metadata(value)
    return value


def validate_metadata(value):
    if (
        set(value) != {"sha", "id", "image", "memory", "actor", "initialize"}
        or not re.fullmatch(r"[0-9a-f]{40}", value["sha"])
        or not re.fullmatch(RELEASE_ID, value["id"])
        or not value["id"].startswith(value["sha"] + "-")
        or not re.fullmatch(IMAGE, value["image"])
        or not re.fullmatch(r"[0-9]{3,4}m", value["memory"])
        or not 256 <= int(value["memory"][:-1]) <= 1024
        or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}(?:\[bot\])?", value["actor"])
        or type(value["initialize"]) is not bool
    ):
        raise ValueError("Invalid release metadata")


class StaleRelease(ValueError):
    """A valid selection superseded by a newer dev commit; safe to skip at selection."""


def require_latest(sha, token):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/git/ref/heads/dev",
        headers={
            "Authorization": "Bearer " + token.strip(),
            "User-Agent": "hanmaum-ai-deploy",
            "Accept": "application/vnd.github+json",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            head = json.load(response)["object"]["sha"]
    except Exception:
        raise ValueError("Cannot verify current dev; deployment stopped") from None
    if head != sha:
        raise StaleRelease("Stale release; start a NEW workflow on current dev")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("write", "latest"))
    parser.add_argument("directory", type=Path, nargs="?")
    args = parser.parse_args()
    if args.command == "latest":
        require_latest(os.environ["RELEASE_SHA"], os.environ["GH_TOKEN"])
    else:
        if args.directory is None:
            parser.error("write requires the private payload directory")
        values = runtime_values(os.environ)
        info = metadata(os.environ)
        write_private(
            args.directory / "runtime.env", "".join(f"{k}={v}\n" for k, v in values.items())
        )
        write_private(args.directory / "release.json", json.dumps(info) + "\n")


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        # Validation messages contain field names/rejection reasons, never input values.
        raise SystemExit(str(error)) from None
    except (KeyError, OSError):
        raise SystemExit(
            "Invalid deployment configuration; check the documented settings"
        ) from None
