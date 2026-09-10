"""Source-independent contracts for mapping workflows."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LifecycleStatus(StrEnum):
    """Processing state for a run or input."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class DecisionStatus(StrEnum):
    """Mapping outcome independent of the processing lifecycle."""

    MAPPED = "mapped"
    AMBIGUOUS = "ambiguous"
    UNMAPPABLE = "unmappable"
    NEEDS_REVIEW = "needs_review"


class MappingRunSpec(BaseModel):
    """Stable identity and metadata for one mapping run."""

    model_config = ConfigDict(extra="forbid")

    source_namespace: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=1, max_length=128)
    source_snapshot: dict[str, Any] = Field(default_factory=dict)
    target_system: str = Field(min_length=1)
    target_release: str | None = None
    algorithm_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    lifecycle_status: LifecycleStatus = LifecycleStatus.PENDING


class MappingInputSpec(BaseModel):
    """Source item presented to a mapping workflow."""

    model_config = ConfigDict(extra="forbid")

    source_namespace: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    source_key: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=1, max_length=128)
    normalized_projection: dict[str, Any] = Field(default_factory=dict)
    lifecycle_status: LifecycleStatus = LifecycleStatus.PENDING
    retry_count: int = Field(default=0, ge=0)
    last_error: str | None = None


class MappingCandidateSpec(BaseModel):
    """One candidate target for a mapping input."""

    model_config = ConfigDict(extra="forbid")

    target_namespace: str = Field(min_length=1)
    target_vocabulary_id: str | None = None
    target_concept_id: str | None = None
    target_code: str | None = None
    target_grain: str | None = None
    target_role: str | None = None
    method: str = Field(min_length=1)
    rank: int = Field(default=1, ge=1)
    score: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    rationale: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_vocabulary_id", "target_concept_id", "target_code")
    @classmethod
    def reject_blank_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("target identifiers cannot be blank")
        return value


class MappingEvidenceSpec(BaseModel):
    """Evidence attached to a candidate or decision."""

    model_config = ConfigDict(extra="forbid")

    evidence_key: str = Field(min_length=1, max_length=128)
    evidence_type: str = Field(min_length=1)
    source_reference: str | None = None
    method: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MappingDecisionSpec(BaseModel):
    """Versioned decision for one mapping input."""

    model_config = ConfigDict(extra="forbid")

    decision_status: DecisionStatus
    selected_candidate_ids: list[str] = Field(default_factory=list)
    outcome_code: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    decided_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
