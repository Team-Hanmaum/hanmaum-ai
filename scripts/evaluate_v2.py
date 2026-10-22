"""Explicitly paid, bounded synthetic evaluation; never run by pytest or CI.

Run: uv run --locked python -m scripts.evaluate_v2 --live --max-calls 20
Reports contain metrics/check names only, not source, generated text or secrets.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pydantic import SecretBytes

from app.errors import AnalysisFailure
from app.v2.comparison import RequestDigester
from app.v2.config import V2Settings
from app.v2.openai_provider import OpenAIProvider
from app.v2.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from app.v2.receipts import ReceiptGate, SQLiteReceiptStore
from app.v2.services import AnalysisService
from app.v2.structured_output import output_schema
from app.v2.validation import parse_request
from evals.v2_cases import REVISION, cases, grade


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


async def evaluate(selected, config, database, repeats=1):
    provider = OpenAIProvider(config)
    key = SecretBytes(b"synthetic-evaluation-hmac-key-only-001")
    store = SQLiteReceiptStore(database, lock_timeout_seconds=config.store_lock_timeout_seconds)
    store.initialize()
    service = AnalysisService(
        receipts=ReceiptGate(store, RequestDigester(keys={"eval": key}, active_key_version="eval")),
        provider=provider,
        timeout_seconds=config.provider_timeout_seconds,
    )
    rows = []
    try:
        for repeat in range(repeats):
            for case in selected:
                request = parse_request(json.dumps(case["request"] | {"requestId": str(uuid4())}))
                started = perf_counter()
                calls_before = provider.calls
                failures, error_code = [], None
                try:
                    result = await service.analyze_request(
                        request,
                        deadline=asyncio.get_running_loop().time() + config.total_timeout_seconds,
                    )
                    failures = grade(case, result)
                except AnalysisFailure as error:
                    error_code = error.code
                # Bound cleanup waiting; a still-running call must not inherit older metrics.
                pending = set(service.tasks)
                if pending:
                    await asyncio.wait(pending, timeout=1)
                observation = provider.last_observation
                matched = (
                    provider.calls == calls_before + 1
                    and observation is not None
                    and observation.call_number == provider.calls
                )
                row = {
                    "caseId": case["caseId"],
                    "repeat": repeat + 1,
                    "contractPassed": error_code is None,
                    "semanticPassed": error_code is None and not failures,
                    "failedChecks": failures,
                    "errorCode": error_code,
                    "elapsedSeconds": round(perf_counter() - started, 6),
                    "providerCalls": provider.calls - calls_before,
                    "provider": asdict(observation) if matched else None,
                }
                rows.append(row)
                print(json.dumps(row, ensure_ascii=True), flush=True)
                # Stop spending after a transport/account failure or timeout; no next case.
                if error_code in {"AI_UNAVAILABLE", "AI_TIMEOUT"}:
                    return rows, provider.calls
    finally:
        await provider.close()
    return rows, provider.calls


def summarize(rows):
    latencies = sorted(row["elapsedSeconds"] for row in rows)
    usages = [row["provider"] for row in rows if row["provider"]]
    return {
        "completedCases": len(rows),
        "contractPassed": sum(r["contractPassed"] for r in rows),
        "semanticPassed": sum(r["semanticPassed"] for r in rows),
        "within20Seconds": sum(t <= 20 for t in latencies),
        "p50Seconds": latencies[math.ceil(len(latencies) * 0.5) - 1] if latencies else None,
        "p95Seconds": latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None,
        "inputTokens": sum(u["input_tokens"] or 0 for u in usages),
        "outputTokens": sum(u["output_tokens"] or 0 for u in usages),
        "usageMissingCalls": sum(
            row["providerCalls"]
            if row["provider"] is None
            else int(row["provider"]["input_tokens"] is None)
            for row in rows
        ),
    }


def reserve_report(path):
    """Exclusive creation before any paid call; keep interrupted reservations as evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            {"status": "in_progress", "message": "Reserved; evaluation may be interrupted"}, stream
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def publish_report(path, report):
    """Replace our reservation only after a complete report has been written beside it."""
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            staged = Path(stream.name)
            json.dump(report, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly allow paid synthetic calls")
    parser.add_argument("--max-calls", type=int, default=20)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--case", action="append", choices=[c["caseId"] for c in cases()])
    parser.add_argument("--output", type=Path, default=Path("test-results/v2-live.json"))
    args = parser.parse_args()
    selected = [c for c in cases() if not args.case or c["caseId"] in args.case]
    if not args.live or not 1 <= args.max_calls <= 60 or not 1 <= args.repeat <= 3:
        parser.error("Use --live, --max-calls 1..60 and --repeat 1..3")
    if len(selected) * args.repeat > args.max_calls:
        parser.error("Selected cases exceed the explicit call budget")
    try:
        reserve_report(args.output)
    except FileExistsError:
        parser.error("Use a new report path; previous evaluation evidence is preserved")
    # Local receipt database is isolated and disposable; it never touches the running server's DB.
    with tempfile.TemporaryDirectory(prefix="hanmaum-synthetic-") as temporary:
        database = Path(temporary) / "receipts.sqlite3"
        config = V2Settings(
            provider="openai",
            receipt_db_path=database,
            hmac_keys={"eval": SecretBytes(b"synthetic-evaluation-hmac-key-only-001")},
            active_hmac_key_version="eval",
        )
        rows, calls_count = asyncio.run(evaluate(selected, config, database, args.repeat))
    report = {
        "createdAt": datetime.now(UTC).isoformat(),
        "datasetRevision": REVISION,
        "datasetSha256": digest(cases()),
        "promptVersion": PROMPT_VERSION,
        "promptSha256": digest(SYSTEM_PROMPT),
        "schemaSha256": digest(output_schema()),
        "model": config.model,
        "reasoningEffort": config.reasoning_effort,
        "serviceTier": config.service_tier,
        "maxOutputTokens": config.max_output_tokens,
        "requestedCases": len(selected) * args.repeat,
        "providerCalls": calls_count,
        "summary": summarize(rows),
        "results": rows,
    }
    publish_report(args.output, report)
    print(json.dumps(report["summary"], ensure_ascii=True))
    if len(rows) != report["requestedCases"] or not all(r["semanticPassed"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
