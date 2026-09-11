"""SQLAlchemy persistence models for the shared mapping contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    column,
    or_,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for groundstore tables."""


class MappingRun(Base):
    __tablename__ = "mapping_runs"
    __table_args__ = (
        UniqueConstraint(
            "source_namespace",
            "source_fingerprint",
            "target_system",
            "target_release",
            "algorithm_version",
            "policy_version",
            name="uq_mapping_run_identity",
        ),
        Index("ix_mapping_runs_source_status", "source_namespace", "lifecycle_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    target_system: Mapped[str] = mapped_column(String(100), nullable=False)
    target_release: Mapped[str | None] = mapped_column(String(100))
    algorithm_version: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    inputs: Mapped[list[MappingInput]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class MappingInput(Base):
    __tablename__ = "mapping_inputs"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "source_namespace",
            "source_kind",
            "source_key",
            "source_fingerprint",
            name="uq_mapping_input_identity",
        ),
        Index("ix_mapping_inputs_run_status", "run_id", "lifecycle_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_projection: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    lifecycle_status: Mapped[str] = mapped_column(String(20), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[MappingRun] = relationship(back_populates="inputs")
    candidates: Mapped[list[MappingCandidate]] = relationship(
        back_populates="input", cascade="all, delete-orphan"
    )
    evidence: Mapped[list[MappingEvidence]] = relationship(
        back_populates="input", cascade="all, delete-orphan"
    )
    decisions: Mapped[list[MappingDecision]] = relationship(
        back_populates="input", cascade="all, delete-orphan"
    )


class MappingCandidate(Base):
    __tablename__ = "mapping_candidates"
    __table_args__ = (
        UniqueConstraint("input_id", "candidate_key", name="uq_mapping_candidate_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    input_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_inputs.id", ondelete="CASCADE"), nullable=False
    )
    candidate_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    target_vocabulary_id: Mapped[str | None] = mapped_column(String(100))
    target_concept_id: Mapped[str | None] = mapped_column(String(100))
    target_code: Mapped[str | None] = mapped_column(String(255))
    target_grain: Mapped[str | None] = mapped_column(String(100))
    target_role: Mapped[str | None] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    input: Mapped[MappingInput] = relationship(back_populates="candidates")
    evidence: Mapped[list[MappingEvidence]] = relationship(back_populates="candidate")
    decisions: Mapped[list[MappingDecision]] = relationship(
        secondary="mapping_decision_candidates", back_populates="selected_candidates"
    )


class MappingEvidence(Base):
    __tablename__ = "mapping_evidence"
    __table_args__ = (
        UniqueConstraint("input_id", "evidence_key", name="uq_mapping_evidence_key"),
        CheckConstraint(
            or_(
                and_(
                    column("candidate_id").is_not(None),
                    column("decision_id").is_(None),
                ),
                and_(
                    column("candidate_id").is_(None),
                    column("decision_id").is_not(None),
                ),
            ),
            name="ck_mapping_evidence_one_attachment",
        ),
        Index("ix_mapping_evidence_candidate", "candidate_id"),
        Index("ix_mapping_evidence_decision", "decision_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    input_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_inputs.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("mapping_candidates.id", ondelete="CASCADE")
    )
    decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("mapping_decisions.id", ondelete="CASCADE")
    )
    evidence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    input: Mapped[MappingInput] = relationship(back_populates="evidence")
    candidate: Mapped[MappingCandidate | None] = relationship(back_populates="evidence")
    decision: Mapped[MappingDecision | None] = relationship(back_populates="evidence")


class MappingDecision(Base):
    __tablename__ = "mapping_decisions"
    __table_args__ = (
        UniqueConstraint("input_id", "decision_version", name="uq_mapping_decision_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    input_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_inputs.id", ondelete="CASCADE"), nullable=False
    )
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_status: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome_code: Mapped[str | None] = mapped_column(String(150))
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    input: Mapped[MappingInput] = relationship(back_populates="decisions")
    selected_candidates: Mapped[list[MappingCandidate]] = relationship(
        secondary="mapping_decision_candidates", back_populates="decisions"
    )
    evidence: Mapped[list[MappingEvidence]] = relationship(back_populates="decision")
    history: Mapped[list[MappingDecisionEvent]] = relationship(
        back_populates="decision", cascade="all, delete-orphan"
    )


class MappingDecisionCandidate(Base):
    __tablename__ = "mapping_decision_candidates"

    decision_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_decisions.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_candidates.id", ondelete="CASCADE"), primary_key=True
    )


class MappingDecisionEvent(Base):
    __tablename__ = "mapping_decision_events"
    __table_args__ = (Index("ix_mapping_decision_events_decision", "decision_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_decisions.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    decision: Mapped[MappingDecision] = relationship(back_populates="history")
