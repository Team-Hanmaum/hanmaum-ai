"""Independent, pre-run holdout for deletion versus unspecified replacement values."""

from evals.v2_cases import candidate, expected, uid

REVISION = "synthetic-change-holdout-v2-20261023.1"


def cases():
    task_fields = ["title", "description", "dueDate", "status"]
    schedule_fields = [
        "title",
        "description",
        "scheduledDate",
        "scheduledTime",
        "location",
        "status",
    ]
    unknown_time = expected(
        "UPDATE",
        "SCHEDULE",
        target=84002,
        relation="UNDETERMINED",
        reasons=["VALUE_UNCLEAR"],
        absent=schedule_fields,
    )
    mixed = expected(
        "UPDATE",
        "SCHEDULE",
        target=84003,
        relation="RELATED",
        changes={"scheduledTime": "16:30"},
        reasons=["VALUE_UNCLEAR"],
        absent=[field for field in schedule_fields if field != "scheduledTime"],
    )
    # The contract fixes the unclear-value reason and omission, not which of these
    # relationships must describe a known target with an unknown replacement.
    for item in (unknown_time, mixed):
        item["relationKinds"] = ["RELATED", "UNDETERMINED"]

    definitions = [
        (
            "change-holdout-task-deadline-removed",
            "공예 재료 분류 작업에는 마감일을 두지 않기로 했습니다. "
            "현재 기록된 2026년 12월 6일 기한만 삭제하고, 제목과 설명과 진행 상태는 유지해 주세요.",
            candidate(
                "TASK",
                84001,
                values={
                    "title": "공예 재료 분류",
                    "description": "종이와 천을 나누어 보관하기",
                    "status": "IN_PROGRESS",
                    "dueDate": "2026-12-06",
                    "assigneeMembershipId": None,
                },
                text="공예 재료 분류는 2026년 12월 6일까지 진행합니다.",
            ),
            expected(
                "UPDATE",
                "TASK",
                target=84001,
                relation="RELATED",
                clear=["dueDate"],
                absent=task_fields,
            ),
        ),
        (
            "change-holdout-time-replacement-pending",
            "별마루 사진 교실은 시작 시각을 조정하기로만 결정했습니다. "
            "몇 시로 바꿀지는 아직 협의 중입니다. 새 시각이 확정될 때까지 현재 시각을 지우지 말고, "
            "날짜와 장소도 그대로 두세요.",
            candidate(
                number=84002,
                values={
                    "title": "별마루 사진 교실",
                    "description": "사진 촬영 연습",
                    "status": "SCHEDULED",
                    "scheduledDate": "2026-12-04",
                    "scheduledTime": "15:00",
                    "location": "별마루 회관 지하 강의실",
                },
                text="별마루 사진 교실은 2026년 12월 4일 오후 3시, 별마루 회관 지하 강의실입니다.",
            ),
            unknown_time,
        ),
        (
            "change-holdout-confirmed-time-pending-place",
            "오름회관 전시 관람의 시작 시각은 오후 4시 30분으로 변경 확정되었습니다. "
            "장소도 다른 곳으로 옮길 예정이지만 새 장소는 아직 정하지 못했습니다. "
            "장소 값은 유지한 채 확정된 시각만 수정해 주세요. 관람 날짜는 바뀌지 않습니다.",
            candidate(
                number=84003,
                values={
                    "title": "오름회관 전시 관람",
                    "description": "가을 작품 전시 관람",
                    "status": "SCHEDULED",
                    "scheduledDate": "2026-12-09",
                    "scheduledTime": "13:00",
                    "location": "오름회관 1층",
                },
                text="전시 관람은 2026년 12월 9일 오후 1시, 오름회관 1층에서 진행합니다.",
            ),
            mixed,
        ),
    ]
    return [
        {
            "caseId": name,
            "request": {
                "requestId": uid(86001 + index),
                "careSpaceId": uid(86000),
                "recordId": uid(87001 + index),
                "content": content,
                "recordedAt": "2026-12-01T10:00:00+09:00",
                "timezone": "Asia/Seoul",
                "candidates": [item],
            },
            "expected": [expectation],
        }
        for index, (name, content, item, expectation) in enumerate(definitions)
    ]
