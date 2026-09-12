"""Read-only scoring view derived from the verified financial extraction."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.direct_financial_extraction import (
    DocumentSummary,
    ExtractionSummary,
    JobStatus,
)
from app.schemas.financial_analysis import DataStatus, RatioResult, ValueProvenance


class ScoringInputField(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: str
    label: str
    value: Decimal | None = None
    unit: str = "MAD"
    status: DataStatus = "missing"
    provenance: list[ValueProvenance] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ScoringSummary(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    policy_version: str = "reference_workbooks_v1_unapproved"
    policy_status: Literal["unapproved_reference", "approved"] = "unapproved_reference"
    inputs: list[ScoringInputField] = Field(default_factory=list)
    ratios: list[RatioResult] = Field(default_factory=list)
    calculable_ratio_count: int = 0
    total_ratio_count: int = 0
    score: Decimal | None = None
    score_status: Literal["not_computed_policy_unapproved", "computed"] = (
        "not_computed_policy_unapproved"
    )
    warnings: list[str] = Field(default_factory=list)


class ScoringJobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus = "queued"
    stream_url: str
    result_url: str


class ScoringControlView(BaseModel):
    code: str
    status: str
    label: str
    expected: float | None = None
    observed: float | None = None
    difference: float | None = None
    tolerance: float | None = None
    affected_fields: list[str] = Field(default_factory=list)
    message: str = ""


class ScoringJobResult(BaseModel):
    document: DocumentSummary
    extraction: ExtractionSummary
    scoring: ScoringSummary
    controls: list[ScoringControlView] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
