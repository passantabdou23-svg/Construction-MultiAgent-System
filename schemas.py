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
    retrieved_standard: str
    design: DesignUpdatePayload
    procurement: ProcurementResult
    schedule: ScheduleImpact
