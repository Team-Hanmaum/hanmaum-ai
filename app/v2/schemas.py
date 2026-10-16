"""Strict v2 wire models, independent of v1 limits and product state transitions."""

import re
from datetime import date, datetime, time
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)
from pydantic.alias_generators import to_camel


def non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Must not be blank")
    return value  # Validate without stripping or normalizing source text.


def uuid_value(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("Use a UUID string")
    return UUID(value)


def calendar_date(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Use YYYY-MM-DD")
    date.fromisoformat(value)
    return value


def clock_time(value: str) -> str:
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise ValueError("Use HH:mm")
    time.fromisoformat(value)
    return value


def recorded_timestamp(value: str) -> str:
    # Check precision before datetime can silently truncate fractional seconds.
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
        value,
    ):
        raise ValueError("Use seconds, a timezone offset and at most six fractional digits")
    datetime.fromisoformat(value)
    return value  # HMAC canonicalization is not the responsibility of these models.


NonBlank = Annotated[str, AfterValidator(non_blank)]
UuidValue = Annotated[UUID, BeforeValidator(uuid_value)]
CalendarDate = Annotated[str, AfterValidator(calendar_date)]
ClockTime = Annotated[str, AfterValidator(clock_time)]
RecordedTimestamp = Annotated[str, AfterValidator(recorded_timestamp)]

type CareItemType = Literal["SCHEDULE", "TASK", "QUESTION", "OBSERVATION", "GUIDANCE"]
type Operation = Literal["CREATE", "UPDATE", "RESOLVE", "LINK_EVIDENCE"]
type RelationKind = Literal["NEW", "RELATED", "DUPLICATE", "CONFLICT", "UNDETERMINED"]
type ReviewReason = Literal[
    "TARGET_AMBIGUOUS", "TARGET_NOT_FOUND", "VALUE_UNCLEAR", "CONFLICT", "ASSIGNEE_UNRESOLVED"
]
type ScheduleStatus = Literal["SCHEDULED", "COMPLETED", "CANCELED"]
type TaskStatus = Literal["NOT_STARTED", "IN_PROGRESS", "COMPLETED"]
type QuestionStatus = Literal["UNCONFIRMED", "CONFIRMED"]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        revalidate_instances="always",
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


class CurrentValues(ContractModel):
    title: str | None
    description: str | None


class ScheduleValues(CurrentValues):
    status: ScheduleStatus
    scheduled_date: CalendarDate | None
    scheduled_time: ClockTime | None
    location: str | None


class TaskValues(CurrentValues):
    status: TaskStatus
    due_date: CalendarDate | None
    assignee_membership_id: UuidValue | None


class QuestionValues(CurrentValues):
    status: QuestionStatus
    answer_text: str | None


class ObservationValues(CurrentValues):
    observed_date: CalendarDate | None
    observed_time_text: str | None


class GuidanceValues(CurrentValues):
    source_name: str | None
    received_date: CalendarDate | None


class Basis(ContractModel):
    basis_id: UuidValue
    kind: Literal["SOURCE_RECORD", "DIRECT_INPUT"]
    text: NonBlank


class CandidateBase(ContractModel):
    item_id: UuidValue
    version: StrictInt
    basis: list[Basis]

    @model_validator(mode="after")
    def unique_basis(self) -> Self:
        identifiers = [basis.basis_id for basis in self.basis]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Basis IDs must be unique within a candidate")
        return self


class ScheduleCandidate(CandidateBase):
    type: Literal["SCHEDULE"]
    current_values: ScheduleValues


class TaskCandidate(CandidateBase):
    type: Literal["TASK"]
    current_values: TaskValues


class QuestionCandidate(CandidateBase):
    type: Literal["QUESTION"]
    current_values: QuestionValues


class ObservationCandidate(CandidateBase):
    type: Literal["OBSERVATION"]
    current_values: ObservationValues


class GuidanceCandidate(CandidateBase):
    type: Literal["GUIDANCE"]
    current_values: GuidanceValues


CandidateItem = Annotated[
    ScheduleCandidate
    | TaskCandidate
    | QuestionCandidate
    | ObservationCandidate
    | GuidanceCandidate,
    Field(discriminator="type"),
]


class AnalysisRequest(ContractModel):
    request_id: UuidValue
    care_space_id: UuidValue
    record_id: UuidValue
    content: NonBlank
    recorded_at: RecordedTimestamp
    timezone: Literal["Asia/Seoul"]
    candidates: list[CandidateItem]

    @model_validator(mode="after")
    def unique_candidates(self) -> Self:
        identifiers = [candidate.item_id for candidate in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Candidate IDs must be unique")
        return self


class BasisReference(ContractModel):
    item_id: UuidValue
    basis_ids: list[UuidValue] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_basis(self) -> Self:
        if len(self.basis_ids) != len(set(self.basis_ids)):
            raise ValueError("Referenced basis IDs must be unique")
        return self


RELATIONS: dict[Operation, set[RelationKind]] = {
    "CREATE": {"NEW"},
    "UPDATE": {"RELATED", "CONFLICT", "UNDETERMINED"},
    "RESOLVE": {"RELATED", "CONFLICT", "UNDETERMINED"},
    "LINK_EVIDENCE": {"DUPLICATE", "UNDETERMINED"},
}
CHANGE_FIELDS: dict[CareItemType, set[str]] = {
    "SCHEDULE": {"title", "description", "scheduledDate", "scheduledTime", "location", "status"},
    "TASK": {"title", "description", "dueDate", "status"},
    "QUESTION": {"title", "description"},
    "OBSERVATION": {"title", "description", "observedDate", "observedTimeText"},
    "GUIDANCE": {"title", "description", "sourceName", "receivedDate"},
}
STATUSES: dict[CareItemType, set[str]] = {
    "SCHEDULE": {"SCHEDULED", "COMPLETED", "CANCELED"},
    "TASK": {"NOT_STARTED", "IN_PROGRESS", "COMPLETED"},
}
TARGET_REASONS = {"TARGET_AMBIGUOUS", "TARGET_NOT_FOUND"}


class Suggestion(ContractModel):
    operation: Operation
    type: CareItemType
    target_item_id: UuidValue | None
    expected_version: StrictInt | None
    candidate_item_ids: list[UuidValue]
    title: NonBlank
    evidence: NonBlank
    changes: dict[str, NonBlank]
    clear_fields: list[str]
    relation_kind: RelationKind
    review_reasons: list[ReviewReason]
    basis_references: list[BasisReference]

    @model_validator(mode="after")
    def consistent_target_and_relation(self) -> Self:
        for values in (
            self.candidate_item_ids,
            self.review_reasons,
            self.clear_fields,
            [ref.item_id for ref in self.basis_references],
        ):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate list entries are not allowed")
        reasons = set(self.review_reasons)
        target_reasons = reasons & TARGET_REASONS
        if self.relation_kind not in RELATIONS[self.operation]:
            raise ValueError("Relation is incompatible with operation")
        if self.operation == "RESOLVE" and self.type != "QUESTION":
            raise ValueError("Only QUESTION can be resolved")
        if "ASSIGNEE_UNRESOLVED" in reasons and self.type != "TASK":
            raise ValueError("Assignee review is only valid for TASK")
        if self.relation_kind == "UNDETERMINED" and not reasons:
            raise ValueError("Undetermined relation requires a review reason")
        if self.relation_kind == "CONFLICT" and (
            "CONFLICT" not in reasons or not self.basis_references
        ):
            raise ValueError("Conflict requires a review reason and comparison basis")

        if self.operation == "CREATE":
            if (
                self.target_item_id is not None
                or self.expected_version is not None
                or self.candidate_item_ids
                or target_reasons
            ):
                raise ValueError("CREATE must not designate an existing target")
        elif self.target_item_id is not None:
            if self.expected_version is None or self.candidate_item_ids or target_reasons:
                raise ValueError("Explicit target requires a version and no target ambiguity")
        else:
            required_reason = "TARGET_AMBIGUOUS" if self.candidate_item_ids else "TARGET_NOT_FOUND"
            if self.expected_version is not None or target_reasons != {required_reason}:
                raise ValueError("Unresolved target requires the matching target review reason")
        return self

    @model_validator(mode="after")
    def valid_changes(self) -> Self:
        if self.operation == "RESOLVE":
            if set(self.changes) != {"answerText"} or self.clear_fields:
                raise ValueError("RESOLVE requires only changes.answerText")
        elif self.operation == "LINK_EVIDENCE":
            if self.changes or self.clear_fields:
                raise ValueError("LINK_EVIDENCE must not change or clear fields")
        else:
            allowed = CHANGE_FIELDS[self.type]
            if not self.changes.keys() <= allowed or not set(self.clear_fields) <= allowed:
                raise ValueError("Field is not allowed for this item type and operation")
            if self.clear_fields and self.operation != "UPDATE":
                raise ValueError("Only UPDATE may clear fields")
            if self.changes.keys() & set(self.clear_fields):
                raise ValueError("A field cannot be changed and cleared together")
            if (
                self.operation == "UPDATE"
                and not self.changes
                and not self.clear_fields
                and not {"ASSIGNEE_UNRESOLVED", "VALUE_UNCLEAR"}.intersection(self.review_reasons)
            ):
                raise ValueError("Empty UPDATE requires assignee or value review")
            for key, value in self.changes.items():
                if key in {"scheduledDate", "dueDate", "observedDate", "receivedDate"}:
                    calendar_date(value)
                elif key == "scheduledTime":
                    clock_time(value)
                elif key == "status" and value not in STATUSES[self.type]:
                    raise ValueError("Invalid status for the item type")
        return self


class AnalysisResponse(ContractModel):
    request_id: UuidValue
    suggestions: list[Suggestion]
