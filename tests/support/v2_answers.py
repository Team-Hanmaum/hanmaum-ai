"""Handwritten, offline answers for semantic-evaluation tests."""

from evals.v2_cases import uid


def handcrafted_response(case):
    """Facts written independently of expected dictionaries, with valid wire metadata."""

    def suggestion(
        operation,
        kind,
        changes,
        *,
        target=None,
        relation="NEW",
        candidates=(),
        reasons=(),
        clear=(),
    ):
        targets = [uid(target)] if target else [uid(number) for number in candidates]
        referenced = [item for item in case["request"]["candidates"] if item["itemId"] in targets]
        return {
            "operation": operation,
            "type": kind,
            "targetItemId": uid(target) if target else None,
            "expectedVersion": 3 if target else None,
            "candidateItemIds": [uid(number) for number in candidates],
            "title": "합성 검토 제안",
            "evidence": case["request"]["content"],
            "changes": changes,
            "clearFields": list(clear),
            "relationKind": relation,
            "reviewReasons": list(reasons),
            "basisReferences": [
                {"itemId": item["itemId"], "basisIds": [item["basis"][0]["basisId"]]}
                for item in referenced
            ],
        }

    responses = {
        "holdout-two-independent-appointments": [
            suggestion(
                "CREATE",
                "SCHEDULE",
                {
                    "title": "도예 수업",
                    "scheduledDate": "2026-11-17",
                    "scheduledTime": "09:00",
                    "location": "별빛공방 3층",
                },
            ),
            suggestion(
                "CREATE",
                "SCHEDULE",
                {
                    "title": "독서 모임",
                    "scheduledDate": "2026-11-19",
                    "scheduledTime": "16:00",
                    "location": "솔잎도서관 1층",
                },
            ),
        ],
        "holdout-answer-and-observation": [
            suggestion(
                "RESOLVE",
                "QUESTION",
                {"answerText": "앞치마 한 벌만 챙기면 됩니다."},
                target=72001,
                relation="RELATED",
            ),
            suggestion(
                "CREATE",
                "OBSERVATION",
                {
                    "description": "퍼즐을 30분 동안 집중해서 맞춤",
                    "observedDate": "2026-11-13",
                    "observedTimeText": "오전",
                },
            ),
        ],
        "holdout-date-change-without-replacement": [
            suggestion(
                "UPDATE",
                "SCHEDULE",
                {},
                target=72002,
                relation="UNDETERMINED",
                reasons=["VALUE_UNCLEAR"],
            )
        ],
        "holdout-two-matching-targets": [
            suggestion(
                "UPDATE",
                "SCHEDULE",
                {"scheduledTime": "14:00"},
                relation="UNDETERMINED",
                candidates=[72003, 72004],
                reasons=["TARGET_AMBIGUOUS"],
            )
        ],
        "holdout-identical-observation": [
            suggestion("LINK_EVIDENCE", "OBSERVATION", {}, target=72005, relation="DUPLICATE")
        ],
        "holdout-contradicting-start-time": [
            suggestion(
                "UPDATE",
                "SCHEDULE",
                {"scheduledTime": "11:00"},
                target=72006,
                relation="CONFLICT",
                reasons=["CONFLICT"],
            )
        ],
        "holdout-direct-input-task-completed": [
            suggestion("UPDATE", "TASK", {"status": "COMPLETED"}, target=72007, relation="RELATED")
        ],
        "holdout-direct-input-explicit-clear": [
            suggestion(
                "UPDATE", "SCHEDULE", {}, target=72008, relation="RELATED", clear=["location"]
            )
        ],
        "holdout-untrusted-instruction-in-source": [
            suggestion(
                "CREATE",
                "OBSERVATION",
                {
                    "description": "색칠 활동을 15분 동안 함",
                    "observedDate": "2026-11-13",
                    "observedTimeText": "오후",
                },
            )
        ],
        "holdout-guidance-explicit-received-day": [
            suggestion(
                "CREATE",
                "GUIDANCE",
                {
                    "sourceName": "솔바람문화관",
                    "description": "이용할 때 개인 컵 가져오기",
                    "receivedDate": "2026-11-12",
                },
            )
        ],
        "holdout-guidance-received-day-unknown": [
            suggestion(
                "CREATE",
                "GUIDANCE",
                {"sourceName": "솔바람문화관", "description": "이용할 때 개인 컵 가져오기"},
            )
        ],
        "holdout-relative-day-across-utc-boundary": [
            suggestion(
                "CREATE",
                "SCHEDULE",
                {
                    "title": "산책 모임",
                    "scheduledDate": "2026-11-14",
                    "scheduledTime": "06:00",
                    "location": "달빛정원 서문",
                },
            )
        ],
    }
    return {"requestId": case["request"]["requestId"], "suggestions": responses[case["caseId"]]}


def answer(case):
    if case["caseId"].startswith("holdout-"):
        return handcrafted_response(case)
    candidates = case["request"]["candidates"]
    if candidates:
        item = candidates[0]
        suggestion = {
            "operation": "UPDATE",
            "type": "SCHEDULE",
            "targetItemId": item["itemId"],
            "expectedVersion": item["version"],
            "relationKind": "UNDETERMINED",
            "reviewReasons": ["VALUE_UNCLEAR"],
            "changes": {},
            "basisReferences": [
                {"itemId": item["itemId"], "basisIds": [item["basis"][0]["basisId"]]}
            ],
        }
    else:
        changes = {"sourceName": "가상센터"}
        if case["caseId"] == "create-guidance-received-date":
            changes["receivedDate"] = "2026-10-22"
        suggestion = {
            "operation": "CREATE",
            "type": "GUIDANCE",
            "targetItemId": None,
            "expectedVersion": None,
            "relationKind": "NEW",
            "reviewReasons": [],
            "changes": changes,
            "basisReferences": [],
        }
    suggestion.update(
        candidateItemIds=[], clearFields=[], title="합성 검토", evidence=case["request"]["content"]
    )
    return {"requestId": case["request"]["requestId"], "suggestions": [suggestion]}
