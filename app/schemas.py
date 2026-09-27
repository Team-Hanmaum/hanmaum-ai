from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


def non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Must not be blank")
    return value


def java_length_limit(limit: int) -> AfterValidator:
    # Java @Size counts UTF-16 code units; preserve that limit for emoji as well.
    def validate(value: str) -> str:
        if len(value.encode("utf-16-le", errors="surrogatepass")) // 2 > limit:
            raise ValueError(f"Must not exceed {limit} UTF-16 code units")
        return value

    return AfterValidator(validate)


def timestamp_text(value: object) -> object:
    if isinstance(value, datetime) or (isinstance(value, str) and "T" in value):
        return value
    raise ValueError("Use an ISO 8601 timestamp with timezone")


NonBlank = Annotated[str, Field(strict=True, min_length=1), AfterValidator(non_blank)]
Content = Annotated[NonBlank, Field(max_length=10000), java_length_limit(10000)]
Summary = Annotated[NonBlank, Field(max_length=2000), java_length_limit(2000)]
Title = Annotated[NonBlank, Field(max_length=200), java_length_limit(200)]
ChangeValue = Annotated[str, Field(strict=True, max_length=2000), java_length_limit(2000)]
Version = Annotated[StrictInt, Field(ge=0, le=9223372036854775807)]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )


class CareItemType(StrEnum):
    SCHEDULE = "SCHEDULE"
    TASK = "TASK"
    QUESTION = "QUESTION"
    OBSERVATION = "OBSERVATION"
    GUIDANCE = "GUIDANCE"


class Operation(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    RESOLVE = "RESOLVE"


class CandidateItem(ContractModel):
    item_id: UUID
    version: Version
    type: CareItemType
    summary: Summary


class AnalysisRequest(ContractModel):
    request_id: UUID
    care_space_id: UUID
    record_id: UUID
    content: Content
    recorded_at: Annotated[AwareDatetime, BeforeValidator(timestamp_text)]
    timezone: NonBlank
    candidates: list[CandidateItem] = Field(max_length=50)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def unique_candidates(self) -> Self:
        identifiers = [item.item_id for item in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Candidate item IDs must be unique")
        return self


class Suggestion(ContractModel):
    operation: Operation
    type: CareItemType
    target_item_id: UUID | None
    expected_version: Version | None
    candidate_item_ids: list[UUID] = Field(max_length=50)
    title: Title
    evidence: Content
    changes: dict[NonBlank, ChangeValue] = Field(max_length=10)

    @model_validator(mode="after")
    def consistent_target(self) -> Self:
        if len(self.candidate_item_ids) != len(set(self.candidate_item_ids)):
            raise ValueError("Candidate item IDs must be unique")
        if self.operation == Operation.CREATE:
            if (
                self.target_item_id is not None
                or self.expected_version is not None
                or self.candidate_item_ids
            ):
                raise ValueError("CREATE must not refer to an existing item")
        elif self.target_item_id is not None:
            if self.expected_version is None or self.candidate_item_ids:
                raise ValueError(
                    "An explicit target requires a version and no ambiguous candidates"
                )
        elif self.expected_version is not None or not self.candidate_item_ids:
            raise ValueError("An unresolved target requires candidates and no version")
        return self


class AnalysisResponse(ContractModel):
    request_id: UUID
    suggestions: list[Suggestion] = Field(max_length=30)
