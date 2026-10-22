"""Focused regression for unknown replacements versus explicitly requested removal.

Existing cases retain their inputs and expectations; new cases are frozen before
the prompt change and live evaluation. This suite is not an independent holdout.
"""

from evals.v2_cases import candidate, expected, uid
from evals.v2_cases import cases as regression_cases
from evals.v2_holdout import cases as former_holdout_cases

REVISION = "synthetic-change-intent-v2-20261023.1"


def cases():
    result = [
        case
        for case in former_holdout_cases()
        if case["caseId"]
        in {"holdout-date-change-without-replacement", "holdout-direct-input-explicit-clear"}
    ]
    result.extend(
        case
        for case in regression_cases()
        if case["caseId"] in {"create-guidance", "create-guidance-received-date"}
    )
    for number, name, content in [
        (
            81001,
            "change-intent-time-unknown",
            "바다문화관 합창 모임의 시작 시각을 바꾸기로 했습니다. "
            "새 시작 시각은 아직 정하지 않았습니다.",
        ),
        (
            81002,
            "change-intent-location-unknown",
            "바다문화관 합창 모임의 장소를 다른 곳으로 옮길 예정입니다. "
            "새 장소는 아직 정해지지 않았습니다.",
        ),
    ]:
        expectation = expected(
            "UPDATE",
            "SCHEDULE",
            target=number,
            relation="UNDETERMINED",
            reasons=["VALUE_UNCLEAR"],
            absent=["title", "description", "scheduledDate", "scheduledTime", "location", "status"],
        )
        expectation["relationKinds"] = ["RELATED", "UNDETERMINED"]
        result.append(
            {
                "caseId": name,
                "request": {
                    "requestId": uid(number + 100000),
                    "careSpaceId": uid(80000),
                    "recordId": uid(number + 200000),
                    "content": content,
                    "recordedAt": "2026-12-01T09:00:00+09:00",
                    "timezone": "Asia/Seoul",
                    "candidates": [
                        candidate(
                            number=number,
                            values={
                                "title": "바다문화관 합창 모임",
                                "scheduledDate": "2026-12-06",
                                "scheduledTime": "13:00",
                                "location": "바다문화관 소강당",
                            },
                            text="2026년 12월 6일 오후 1시에 바다문화관 소강당에서 합창 모임.",
                        )
                    ],
                },
                "expected": [expectation],
            }
        )
    return result
