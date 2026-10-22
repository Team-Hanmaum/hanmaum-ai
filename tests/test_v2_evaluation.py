"""Offline validity and evaluator sensitivity; never dispatch a live model in pytest."""

import asyncio
import json
import subprocess
import sys
from copy import deepcopy

import httpx
import pytest
from test_v2_openai import Stream, envelope, response, settings

from app.v2.schemas import AnalysisResponse
from app.v2.validation import parse_request
from evals.v2_cases import GRADER_VERSION, cases, expected, grade
from scripts.evaluate_v2 import (
    dataset,
    digest,
    evaluate,
    main,
    publish_report,
    reserve_report,
    summarize,
)


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["caseId"])
def test_every_synthetic_request_satisfies_real_contract(case):
    request = parse_request(json.dumps(case["request"]))
    assert str(request.request_id) == case["request"]["requestId"]
    assert len(case["expected"]) <= 1


def test_evaluation_refuses_implicit_paid_calls(monkeypatch):
    monkeypatch.setattr("sys.argv", ["evaluate_v2"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2


def test_evaluation_refuses_over_budget_before_reading_secrets(monkeypatch):
    monkeypatch.setattr("sys.argv", ["evaluate_v2", "--live", "--max-calls", "1"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2


def test_grader_detects_wrong_facts_and_missing_suggestion():
    case = next(c for c in cases() if c["caseId"] == "update-schedule")
    request = case["request"]
    candidate = request["candidates"][0]
    data = {
        "requestId": request["requestId"],
        "suggestions": [
            {
                "operation": "UPDATE",
                "type": "SCHEDULE",
                "targetItemId": candidate["itemId"],
                "expectedVersion": 3,
                "candidateItemIds": [],
                "title": "시간 변경",
                "evidence": request["content"],
                "changes": {"scheduledTime": "11:00"},
                "clearFields": [],
                "relationKind": "RELATED",
                "reviewReasons": [],
                "basisReferences": [
                    {"itemId": candidate["itemId"], "basisIds": [candidate["basis"][0]["basisId"]]}
                ],
            }
        ],
    }
    assert grade(case, AnalysisResponse.model_validate(data)) == []
    wrong = deepcopy(data)
    wrong["suggestions"][0]["changes"] = {"scheduledTime": "12:00", "location": "2층"}
    assert set(grade(case, AnalysisResponse.model_validate(wrong))) == {
        "changes.scheduledTime",
        "absent.location",
    }
    data["suggestions"] = []
    assert grade(case, AnalysisResponse.model_validate(data)) == ["suggestion_count"]


def test_summary_does_not_count_failed_or_timed_out_calls_as_success():
    summary = summarize(
        [
            {
                "elapsedSeconds": 20.1,
                "contractPassed": False,
                "semanticPassed": False,
                "provider": None,
                "providerCalls": 1,
            }
        ]
    )
    assert summary["contractPassed"] == summary["semanticPassed"] == summary["within20Seconds"] == 0
    assert summary["usageMissingCalls"] == 1


@pytest.mark.anyio
async def test_runner_executes_real_service_with_mock_transport(tmp_path, monkeypatch):
    from app.v2.openai_provider import OpenAIProvider

    monkeypatch.setattr(
        "scripts.evaluate_v2.OpenAIProvider",
        lambda config: OpenAIProvider(
            config,
            transport=httpx.MockTransport(
                lambda _: response(
                    {
                        "status": "completed",
                        "error": None,
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "status": "completed",
                                "content": [{"type": "output_text", "text": '{"suggestions":[]}'}],
                            }
                        ],
                    }
                )
            ),
        ),
    )
    selected = [c for c in cases() if c["caseId"] == "no-care-information"]
    rows, calls_count = await evaluate(selected, settings(tmp_path), tmp_path / "eval.sqlite3")
    assert calls_count == 1 and rows[0]["semanticPassed"]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["timeout", "unavailable"])
async def test_runner_stops_after_failed_paid_call(tmp_path, monkeypatch, failure):
    from app.v2.openai_provider import OpenAIProvider

    def send(_):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-detail")
        return response(status=503)

    provider = OpenAIProvider(settings(tmp_path), transport=httpx.MockTransport(send))
    monkeypatch.setattr("scripts.evaluate_v2.OpenAIProvider", lambda _: provider)
    rows, count = await evaluate(
        cases()[:3], settings(tmp_path), tmp_path / "eval.sqlite3", repeats=2
    )
    assert len(rows) == count == 1
    assert rows[0]["errorCode"] == ("AI_TIMEOUT" if failure == "timeout" else "AI_UNAVAILABLE")
    assert rows[0]["provider"]["call_number"] == 1


@pytest.mark.anyio
async def test_slow_cancel_does_not_reuse_previous_call_metrics(tmp_path, monkeypatch):
    from app.v2.openai_provider import OpenAIProvider

    release = asyncio.Event()
    cancelled = asyncio.Event()
    finished = asyncio.Event()

    class SlowCancel(Stream):
        async def __aiter__(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                raise
            finally:
                finished.set()
            yield b"never"

    requests = 0

    def send(_):
        nonlocal requests
        requests += 1
        return (
            response(envelope({"suggestions": []}))
            if requests == 1
            else response(stream=SlowCancel([]))
        )

    config = settings(tmp_path, provider_timeout_seconds=0.05)
    provider = OpenAIProvider(config, transport=httpx.MockTransport(send))
    monkeypatch.setattr("scripts.evaluate_v2.OpenAIProvider", lambda _: provider)
    selected = [next(c for c in cases() if c["caseId"] == "no-care-information")] * 3
    try:
        rows, count = await asyncio.wait_for(
            evaluate(selected, config, tmp_path / "eval.sqlite3"), 5
        )
        assert cancelled.is_set() and not finished.is_set()
        assert count == 2 and len(rows) == 2
        assert rows[0]["provider"]["input_tokens"] == 120
        assert rows[1]["errorCode"] == "AI_TIMEOUT" and rows[1]["provider"] is None
        summary = summarize(rows)
        assert summary["inputTokens"] == 120 and summary["outputTokens"] == 50
        assert summary["usageMissingCalls"] == 1
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 2)
        await asyncio.sleep(0)


def test_two_processes_cannot_reserve_same_report(tmp_path):
    path = tmp_path / "report.json"
    code = "from pathlib import Path; from scripts.evaluate_v2 import reserve_report; import sys; reserve_report(Path(sys.argv[1]))"
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(2)
    ]
    for process in processes:
        process.communicate(timeout=15)
    assert sorted(p.returncode for p in processes) == [0, 1]
    assert json.loads(path.read_text())["status"] == "in_progress"


def test_main_reserves_before_settings_or_paid_calls_and_keeps_interruption(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    reads = []

    def unavailable_settings(**_):
        reads.append(True)
        assert json.loads(path.read_text())["status"] == "in_progress"
        raise RuntimeError("stop before evaluation")

    monkeypatch.setattr("scripts.evaluate_v2.V2Settings", unavailable_settings)
    monkeypatch.setattr("sys.argv", ["evaluate_v2", "--live", "--output", str(path)])
    with pytest.raises(RuntimeError, match="stop before evaluation"):
        main()
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2 and len(reads) == 1


def test_report_publish_failure_preserves_reservation_then_complete_report(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    reserve_report(path)
    reserved = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            "scripts.evaluate_v2.os.replace", lambda *_: (_ for _ in ()).throw(OSError("failed"))
        )
        with pytest.raises(OSError):
            publish_report(path, {"complete": True})
    assert path.read_bytes() == reserved and list(tmp_path.iterdir()) == [path]
    publish_report(path, {"complete": True})
    with pytest.raises(FileExistsError):
        reserve_report(path)
    assert json.loads(path.read_text()) == {"complete": True}


@pytest.mark.parametrize(
    "name, has_date", [("create-guidance", False), ("create-guidance-received-date", True)]
)
def test_guidance_grader_requires_date_only_with_explicit_received_date(name, has_date):
    case = next(c for c in cases() if c["caseId"] == name)
    suggestion = {
        "operation": "CREATE",
        "type": "GUIDANCE",
        "targetItemId": None,
        "expectedVersion": None,
        "candidateItemIds": [],
        "title": "안내",
        "evidence": case["request"]["content"],
        "changes": {"sourceName": "가상센터"},
        "clearFields": [],
        "relationKind": "NEW",
        "reviewReasons": [],
        "basisReferences": [],
    }

    def check():
        result = AnalysisResponse.model_validate(
            {"requestId": case["request"]["requestId"], "suggestions": [suggestion]}
        )
        return grade(case, result)

    assert check() == (["changes.receivedDate"] if has_date else [])
    suggestion["changes"]["receivedDate"] = "2026-10-22"
    assert check() == ([] if has_date else ["absent.receivedDate"])


def test_multi_grader_uses_augmenting_paths_for_overlapping_expectations():
    case = next(c for c in cases() if c["caseId"] == "create-guidance")
    case["expected"] = [
        expected("CREATE", "GUIDANCE"),
        expected("CREATE", "GUIDANCE", changes={"sourceName": "가상센터"}),
    ]
    suggestions = [
        {
            "operation": "CREATE",
            "type": "GUIDANCE",
            "targetItemId": None,
            "expectedVersion": None,
            "candidateItemIds": [],
            "title": "안내",
            "evidence": "합성 안내",
            "changes": {"sourceName": source},
            "clearFields": [],
            "relationKind": "NEW",
            "reviewReasons": [],
            "basisReferences": [],
        }
        for source in ("가상센터", "가상학교")
    ]
    result = AnalysisResponse.model_validate(
        {"requestId": case["request"]["requestId"], "suggestions": suggestions}
    )
    # A greedy first match would consume the only response satisfying the second expectation.
    assert grade(case, result) == []
    case["expected"][1]["changes"]["sourceName"] = "없는 기관"
    assert grade(case, result) == ["unmatched_expected.1"]


def test_suite_case_mismatch_is_rejected_before_settings(monkeypatch, tmp_path):
    def forbidden(**_):
        pytest.fail("Settings must not be read for an unknown case")

    monkeypatch.setattr("scripts.evaluate_v2.V2Settings", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        ["evaluate_v2", "--live", "--suite", "holdout", "--case", "create-guidance"],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_cli_keeps_suite_and_frozen_dataset_identity_in_report(monkeypatch, tmp_path):
    revision, all_cases = dataset("holdout")
    selected_name = all_cases[0]["caseId"]
    report_path = tmp_path / "holdout.json"

    async def fake_evaluate(selected, config, database, repeats):
        assert [case["caseId"] for case in selected] == [selected_name]
        return [
            {
                "caseId": selected_name,
                "contractPassed": True,
                "semanticPassed": True,
                "elapsedSeconds": 0.1,
                "provider": None,
                "providerCalls": 1,
            }
        ], 1

    monkeypatch.setattr("scripts.evaluate_v2.evaluate", fake_evaluate)
    monkeypatch.setattr("scripts.evaluate_v2.V2Settings", lambda **_: settings(tmp_path))
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_v2",
            "--live",
            "--suite",
            "holdout",
            "--max-calls",
            "1",
            "--case",
            selected_name,
            "--output",
            str(report_path),
        ],
    )
    main()
    report = json.loads(report_path.read_text())
    assert report["suite"] == "holdout" and report["datasetRevision"] == revision
    assert report["evaluationRole"] == "regression-after-feedback"
    assert report["datasetSha256"] == digest(all_cases)
    assert report["selectedCaseIds"] == [selected_name]
    assert report["graderVersion"] == GRADER_VERSION
    assert report["requestedCases"] == 1
