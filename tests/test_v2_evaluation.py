"""Offline validity and evaluator sensitivity; never dispatch a live model in pytest."""

import json
from copy import deepcopy

import httpx
import pytest
from test_v2_openai import response, settings

from app.v2.schemas import AnalysisResponse
from app.v2.validation import parse_request
from evals.v2_cases import cases, grade
from scripts.evaluate_v2 import evaluate, main, summarize


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
            }
        ]
    )
    assert summary["contractPassed"] == summary["semanticPassed"] == summary["within20Seconds"] == 0


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
