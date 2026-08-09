"""Validated data contracts shared by the construction agents."""

from __future__ import annotations

from datetime import date
from enum import Enum
from math import isfinite
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MaterialType(str, Enum):
    CONCRETE = "Concrete"
    STEEL = "Steel"
    REBAR = "Rebar"
    FORMWORK = "Formwork"
    MASONRY = "Masonry"
    TIMBER = "Timber"
    AGGREGATE = "Aggregate"
    CEMENT = "Cement"
    OTHER = "Other"


class MaterialRequirement(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    item_id: str = Field(min_length=2, max_length=80)
    material_type: MaterialType
    specification: str = Field(min_length=2, max_length=500)
    quantity: float = Field(gt=0, le=1_000_000_000)
    unit: str = Field(min_length=1, max_length=40)

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("quantity must be finite")
        return value


class DesignUpdatePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    revision_id: str = Field(pattern=r"^Rev-[A-Z0-9][A-Z0-9-]{0,63}$")
    affected_element: str = Field(min_length=2, max_length=200)
    requirements: List[MaterialRequirement] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def item_ids_must_be_unique(self) -> "DesignUpdatePayload":
        item_ids = [requirement.item_id.casefold() for requirement in self.requirements]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("material requirement item_id values must be unique")
        return self


class EvidenceCitation(BaseModel):
    """A verbatim passage tied to one retrieved, immutable chunk."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chunk_id: str = Field(min_length=8, max_length=200)
    evidence_quote: str = Field(min_length=12, max_length=600)


class GroundedClaim(BaseModel):
    """One technical claim with one or more explicit supporting passages."""

    model_config = ConfigDict(str_strip_whitespace=True)

    claim_id: str = Field(pattern=r"^CLAIM-[A-Z0-9][A-Z0-9-]{0,31}$")
    claim_text: str = Field(min_length=12, max_length=800)
    citations: List[EvidenceCitation] = Field(min_length=1, max_length=5)


class GroundedDesignResponse(BaseModel):
    """Raw model contract: either supported output or an explicit safe refusal."""

    model_config = ConfigDict(str_strip_whitespace=True)

    evidence_status: Literal[
        "SUPPORTED",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
    ]
    reason: str = Field(min_length=8, max_length=800)
    design: DesignUpdatePayload | None = None
    claims: List[GroundedClaim] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def status_controls_payload(self) -> "GroundedDesignResponse":
        if self.evidence_status == "SUPPORTED":
            if self.design is None or not self.claims:
                raise ValueError("SUPPORTED output requires a design and at least one grounded claim")
        elif self.design is not None or self.claims:
            raise ValueError("A refused output cannot contain a design or technical claims")
        return self


class GroundingVerification(BaseModel):
    status: Literal["VERIFIED"] = "VERIFIED"
    verified_claim_count: int = Field(ge=1)
    verified_citation_count: int = Field(ge=1)
    cited_chunk_ids: List[str] = Field(min_length=1)
    notes: List[str] = Field(min_length=1)


class VerifiedDesignResult(BaseModel):
    design: DesignUpdatePayload
    grounded_claims: List[GroundedClaim] = Field(min_length=1)
    grounding: GroundingVerification


class RetrievedEvidence(BaseModel):
    chunk_id: str
    document_id: str
    document_code: str
    title: str
    edition: str
    status: str
    jurisdiction: str
    page_number: int = Field(ge=1)
    printed_page_label: str
    section: str
    clause: str
    similarity: float
    source_url: str


class ReviewDecisionInput(BaseModel):
    """A self-identified human decision; authentication is outside this local prototype."""

    model_config = ConfigDict(str_strip_whitespace=True)

    reviewer_name: str = Field(min_length=2, max_length=120)
    reviewer_role: Literal["Design engineer", "Project manager", "Authorized reviewer"]
    decision: Literal["APPROVE", "REJECT"]
    comment: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def rejection_requires_reason(self) -> "ReviewDecisionInput":
        if self.decision == "REJECT" and len(self.comment) < 10:
            raise ValueError("A rejection requires a comment of at least 10 characters")
        return self


class ApprovalReviewRecord(BaseModel):
    review_id: str
    run_id: str
    revision_id: str
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_name: str | None = None
    reviewer_role: str | None = None
    review_comment: str | None = None
    requested_at: str | None = None
    decided_at: str | None = None


class ApprovalPayload(BaseModel):
    site_note: str = Field(min_length=10, max_length=4_000)
    validation_message: str
    retrieved_standard: str
    retrieved_evidence: List[RetrievedEvidence] = Field(min_length=1)
    grounded_claims: List[GroundedClaim] = Field(min_length=1)
    grounding: GroundingVerification
    design: DesignUpdatePayload


class PendingApprovalResult(ApprovalPayload):
    run_id: str
    status: Literal["AWAITING_APPROVAL"]
    review: ApprovalReviewRecord


class RejectedApprovalResult(BaseModel):
    run_id: str
    status: Literal["REJECTED"]
    validation_message: str
    review: ApprovalReviewRecord
    design: DesignUpdatePayload


class ProcurementQuote(BaseModel):
    """An unverified planning estimate produced by the local LLM."""

    model_config = ConfigDict(str_strip_whitespace=True)

    item_id: str = Field(min_length=2, max_length=80)
    supplier_name: str = Field(min_length=2, max_length=200)
    unit_cost: float = Field(gt=0, le=1_000_000_000)
    total_cost: float = Field(gt=0, le=1_000_000_000_000)
    lead_time_days: int = Field(ge=0, le=365)
    earliest_delivery_date: date
    quote_status: Literal["PENDING_VERIFICATION"] = "PENDING_VERIFICATION"
    source: Literal["LLM_ESTIMATE_UNVERIFIED"] = "LLM_ESTIMATE_UNVERIFIED"

    @field_validator("unit_cost", "total_cost")
    @classmethod
    def costs_must_be_finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("cost must be finite")
        return value


class ProcurementResult(BaseModel):
    revision_id: str
    status: Literal["PENDING_VERIFICATION"]
    quotes: List[ProcurementQuote] = Field(min_length=1)
    maximum_lead_time_days: int = Field(ge=0, le=365)


class ScheduleImpact(BaseModel):
    revision_id: str
    affected_task: str
    task_id: str
    is_critical_path: bool
    delay_days: int = Field(ge=0)
    baseline_duration_days: int = Field(ge=0)
    projected_duration_days: int = Field(ge=0)
    projected_completion_date: date
    recommended_action: str


class PipelineResult(BaseModel):
    run_id: str
    status: Literal["COMPLETED"]
    validation_message: str
    review: ApprovalReviewRecord
    retrieved_standard: str
    retrieved_evidence: List[RetrievedEvidence] = Field(min_length=1)
    grounded_claims: List[GroundedClaim] = Field(min_length=1)
    grounding: GroundingVerification
    design: DesignUpdatePayload
    procurement: ProcurementResult
    schedule: ScheduleImpact
