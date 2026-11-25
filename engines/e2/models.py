"""Typed v2 request and response payloads for the internal service."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from search_shared import SCHEMA_VERSION
from search_shared.index_reader import passage_id_value

from .assistance import MAX_SUGGESTIONS
from .query import MAX_K, MAX_QUERY_LENGTH, MAX_TOP

DocumentId = Annotated[str, Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]
PositiveInt = Annotated[int, Field(ge=1)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Filters(ContractModel):
    state: str | None = None
    department: str | None = None
    category: str | None = None


class SearchRequest(ContractModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    filters: Filters = Field(default_factory=Filters)
    k: int = Field(default=10, ge=1, le=MAX_K)
    page: int = Field(default=1, ge=1, le=MAX_TOP)


class MatchSpan(ContractModel):
    start: int = Field(ge=0)
    end: PositiveInt

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Match span is empty or reversed")
        return self


class SearchHit(ContractModel):
    doc_id: DocumentId
    scheme_id: DocumentId | None
    passage_id: int | str
    title: str | None
    snippet: str
    page_no: PositiveInt | None
    department: str | None
    issue_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None
    score: float = Field(allow_inf_nan=False)
    matched_terms: list[str]
    match_spans: list[MatchSpan]
    source_type: Literal["PDF", "CSV"] | None
    superseded: bool | None
    current_doc_id: DocumentId | None

    @field_validator("passage_id")
    @classmethod
    def canonical_id(cls, value):
        passage_id_value(value)
        return value


class SearchResponse(ContractModel):
    results: list[SearchHit] = Field(max_length=MAX_K)
    did_you_mean: str | None
    total: int = Field(ge=0)
    page: PositiveInt
    k: PositiveInt
    generation: int = Field(ge=0)


class AutocompleteResponse(ContractModel):
    suggestions: list[str] = Field(max_length=MAX_SUGGESTIONS)


class HealthResponse(ContractModel):
    status: Literal["ok"]
    service: Literal["e2"]
    contract_version: PositiveInt

    @field_validator("contract_version")
    @classmethod
    def supported_version(cls, value):
        if value != SCHEMA_VERSION:
            raise ValueError("Unsupported contract version")
        return value


class ErrorDetail(ContractModel):
    code: Literal["INVALID_REQUEST", "SERVICE_UNAVAILABLE", "SEARCH_TIMEOUT"]
    message: str
    request_id: DocumentId


class ErrorResponse(ContractModel):
    error: ErrorDetail
