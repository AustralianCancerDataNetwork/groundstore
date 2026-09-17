"""Source-independent contracts for mapping workflows."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class DecisionOrigin(StrEnum):
    """How the latest mapping decision was produced."""

    ALGORITHM = "algorithm"
    MANUAL_OVERRIDE = "manual_override"
    OPERATOR_REVIEW = "operator_review"


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

    @model_validator(mode="after")
    def require_target_identifier(self) -> MappingCandidateSpec:
        if self.target_concept_id is None and self.target_code is None:
            raise ValueError(
                "a candidate must provide target_concept_id or target_code"
            )
        return self


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
    decision_origin: DecisionOrigin | None = None


class MappingOverrideSpec(BaseModel):
    """A durable, source-specific mapping decision guarded by a fingerprint."""

    model_config = ConfigDict(extra="forbid")

    source_namespace: str = Field(min_length=1, max_length=100)
    source_kind: str = Field(min_length=1, max_length=100)
    source_identity: str = Field(min_length=1, max_length=255)
    target_system: str = Field(min_length=1, max_length=100)
    decision_status: Literal[DecisionStatus.MAPPED, DecisionStatus.UNMAPPABLE]
    target_reference: dict[str, Any] | None = None
    confirmed_fingerprint: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1)
    authored_by: str = Field(min_length=1, max_length=255)

    @field_validator("source_namespace", "source_kind", "source_identity", "target_system", "confirmed_fingerprint", "authored_by")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("override identity and provenance fields cannot be blank")
        return value

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_target_reference(self) -> MappingOverrideSpec:
        if self.decision_status is DecisionStatus.MAPPED and not self.target_reference:
            raise ValueError("mapped overrides require a target_reference")
        if self.decision_status is DecisionStatus.UNMAPPABLE and self.target_reference:
            raise ValueError("unmappable overrides cannot include a target_reference")
        return self


class MappingOverrideImportResult(BaseModel):
    """Outcome of an all-or-nothing override import."""

    model_config = ConfigDict(extra="forbid")

    created: int = Field(default=0, ge=0)
    replaced: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
