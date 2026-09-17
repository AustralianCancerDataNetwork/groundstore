"""Read-only views over persisted mapping work.

The persistence API remains useful to standalone writers.  This module adds
the small, transport-neutral packet that a host or review client can consume
without depending on SQLAlchemy model instances or lazy relationships.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    MappingCandidate,
    MappingDecision,
    MappingDecisionEvent,
    MappingEvidence,
    MappingInput,
    MappingRun,
)
from .store import MappingStore


class MappingEvidencePacket(BaseModel):
    """Stable JSON contract for reviewing one persisted mapping input."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "groundstore.mapping-evidence-packet.v1"
    run: dict[str, Any]
    input: dict[str, Any]
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    decision: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class MappingReviewHandoff(BaseModel):
    """Review-task reference carrying a self-contained evidence packet."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "groundstore.mapping-review-handoff.v1"
    task_id: str = Field(min_length=1)
    task_type: str = "mapping_review"
    source_namespace: str = Field(min_length=1)
    input_id: str = Field(min_length=1)
    decision_status: str = "needs_review"
    packet: MappingEvidencePacket
    requested_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MappingReviewPage(BaseModel):
    """Stable, paginated summary for selecting mapping inputs to review."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "groundstore.mapping-review-page.v1"
    run: dict[str, Any] | None = None
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    page_count: int = Field(ge=0)
    total_items: int = Field(ge=0)
    decision_status: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


class MappingRunSummary(BaseModel):
    """Stable JSON contract for one run's metadata and coverage counts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "groundstore.mapping-run-summary.v1"
    run_id: str
    source_namespace: str
    source_fingerprint: str
    source_snapshot: dict[str, Any]
    target_system: str
    target_release: str | None
    algorithm_version: str
    policy_version: str
    run_lifecycle_status: str
    last_error: str | None
    created_at: str
    updated_at: str
    input_count: int = Field(ge=0)
    lifecycle_counts: dict[str, int] = Field(default_factory=dict)
    decision_counts: dict[str, int] = Field(default_factory=dict)
    decision_origin_counts: dict[str, int] = Field(default_factory=dict)


class MappingProgress(BaseModel):
    """Stable JSON contract for polling one mapping run's progress."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "groundstore.mapping-progress.v1"
    run_id: str
    run_status: str
    total_inputs: int = Field(ge=0)
    queued_count: int = Field(ge=0)
    active_count: int = Field(ge=0)
    complete_count: int = Field(ge=0)
    retryable_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    remaining_count: int = Field(ge=0)
    decision_counts: dict[str, int] = Field(default_factory=dict)
    decision_origin_counts: dict[str, int] = Field(default_factory=dict)
    override_count: int = Field(default=0, ge=0)
    last_updated_at: str


@dataclass(frozen=True, slots=True)
class MappingReadContext:
    """Read-only Groundstore façade for host tools and review consumers."""

    store: MappingStore

    def status(
        self,
        source_namespace: str,
        *,
        target_system: str | None = None,
    ) -> dict[str, Any]:
        """Return the latest complete run and its common coverage summary."""

        run = self.store.latest_successful_run(source_namespace, target_system=target_system)
        if run is None:
            return {
                "source_namespace": source_namespace,
                "target_system": target_system,
                "latest_run": None,
                "coverage": None,
            }
        return {
            "source_namespace": source_namespace,
            "target_system": target_system,
            "latest_run": _run_payload(run),
            "coverage": self.store.coverage(run.id),
        }

    def run_summary(self, run_id: str) -> MappingRunSummary:
        """Return a detached, JSON-safe summary for one mapping run."""
        return MappingRunSummary.model_validate(self.store.run_summary(run_id))

    def progress(self, run_id: str) -> MappingProgress:
        """Return a detached, JSON-safe progress snapshot for one run."""
        return MappingProgress.model_validate(self.store.progress(run_id))

    def evidence_packet(self, input_id: str) -> MappingEvidencePacket:
        """Build a detached packet with candidates, evidence, and decision history."""

        input_record = self.store.get_input(input_id)
        if input_record is None:
            raise KeyError(f"unknown mapping input: {input_id}")
        run = self.store.get_run(input_record.run_id)
        if run is None:  # pragma: no cover - protected by the database FK
            raise KeyError(f"unknown mapping run: {input_record.run_id}")

        candidates = self.store.get_candidates(input_id)
        evidence = self.store.get_evidence(input_id)
        decision = self.store.latest_decision(input_id)
        selected_ids = (
            self.store.get_decision_candidate_ids(decision.id) if decision is not None else []
        )
        events = self.store.get_decision_history(decision.id) if decision is not None else []
        evidence_by_candidate: dict[str, list[dict[str, Any]]] = {}
        unattached: list[dict[str, Any]] = []
        for item in evidence:
            payload = _evidence_payload(item)
            if item.candidate_id is None:
                unattached.append(payload)
            else:
                evidence_by_candidate.setdefault(item.candidate_id, []).append(payload)

        candidate_payloads = []
        for candidate in candidates:
            payload = _candidate_payload(candidate)
            payload["evidence"] = evidence_by_candidate.get(candidate.id, [])
            candidate_payloads.append(payload)

        decision_payload = None
        if decision is not None:
            decision_payload = _decision_payload(decision)
            decision_payload["selected_candidate_ids"] = selected_ids
            decision_payload["history"] = [_event_payload(event) for event in events]

        return MappingEvidencePacket(
            run=_run_payload(run),
            input=_input_payload(input_record),
            candidates=candidate_payloads,
            evidence=unattached,
            decision=decision_payload,
        )

    def review_page(
        self,
        source_namespace: str,
        *,
        run_id: str | None = None,
        target_system: str | None = None,
        page: int = 1,
        page_size: int = 20,
        decision_status: str | None = None,
    ) -> MappingReviewPage:
        """Return a database-paginated review summary for one mapping run."""
        if page < 1:
            raise ValueError("page must be positive")
        if page_size < 1:
            raise ValueError("page_size must be positive")

        run = self.store.get_run(run_id) if run_id is not None else None
        if run_id is None:
            run = self.store.latest_successful_run(
                source_namespace, target_system=target_system
            )
        if run is None:
            return MappingReviewPage(
                run=None,
                page=page,
                page_size=page_size,
                page_count=0,
                total_items=0,
                decision_status=decision_status,
            )
        if run.source_namespace != source_namespace:
            raise ValueError(f"mapping run {run.id} does not belong to {source_namespace}")
        if target_system is not None and run.target_system != target_system:
            raise ValueError(f"mapping run {run.id} does not target {target_system}")

        records, total = self.store.get_review_inputs(
            run.id,
            offset=(page - 1) * page_size,
            limit=page_size,
            decision_status=decision_status,
        )
        page_count = (total + page_size - 1) // page_size
        return MappingReviewPage(
            run=_run_payload(run),
            page=page,
            page_size=page_size,
            page_count=page_count,
            total_items=total,
            decision_status=decision_status,
            items=[
                {
                    "input_id": input_record.id,
                    "source_namespace": input_record.source_namespace,
                    "source_kind": input_record.source_kind,
                    "source_key": input_record.source_key,
                    "lifecycle_status": input_record.lifecycle_status,
                    "decision_status": status,
                    "candidate_count": len(candidates),
                    "candidates": [
                        _review_candidate_payload(candidate) for candidate in candidates
                    ],
                    "normalized_projection": input_record.normalized_projection,
                }
                for input_record, status, candidates in records
            ],
        )

    def review_handoff(
        self,
        input_id: str,
        *,
        requested_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MappingReviewHandoff:
        """Return a deterministic review reference without mutating the store."""

        packet = self.evidence_packet(input_id)
        decision_status = (
            packet.decision.get("decision_status", "needs_review")
            if packet.decision
            else "needs_review"
        )
        return MappingReviewHandoff(
            task_id=f"mapping-review:{input_id}",
            source_namespace=packet.input["source_namespace"],
            input_id=input_id,
            decision_status=str(decision_status),
            packet=packet,
            requested_by=requested_by,
            metadata=metadata or {},
        )


def _run_payload(run: MappingRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "source_namespace": run.source_namespace,
        "source_fingerprint": run.source_fingerprint,
        "source_snapshot": run.source_snapshot,
        "target_system": run.target_system,
        "target_release": run.target_release,
        "algorithm_version": run.algorithm_version,
        "policy_version": run.policy_version,
        "lifecycle_status": run.lifecycle_status,
        "last_error": run.last_error,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _input_payload(input_record: MappingInput) -> dict[str, Any]:
    return {
        "id": input_record.id,
        "run_id": input_record.run_id,
        "source_namespace": input_record.source_namespace,
        "source_kind": input_record.source_kind,
        "source_key": input_record.source_key,
        "source_fingerprint": input_record.source_fingerprint,
        "normalized_projection": input_record.normalized_projection,
        "lifecycle_status": input_record.lifecycle_status,
        "retry_count": input_record.retry_count,
        "last_error": input_record.last_error,
        "created_at": input_record.created_at.isoformat(),
        "updated_at": input_record.updated_at.isoformat(),
    }


def _candidate_payload(candidate: MappingCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "target_namespace": candidate.target_namespace,
        "target_vocabulary_id": candidate.target_vocabulary_id,
        "target_concept_id": candidate.target_concept_id,
        "target_code": candidate.target_code,
        "target_grain": candidate.target_grain,
        "target_role": candidate.target_role,
        "method": candidate.method,
        "rank": candidate.rank,
        "score": candidate.score,
        "confidence": candidate.confidence,
        "rationale": candidate.rationale,
        "metadata": candidate.metadata_,
        "created_at": candidate.created_at.isoformat(),
    }


def _review_candidate_payload(candidate: MappingCandidate) -> dict[str, Any]:
    payload = _candidate_payload(candidate)
    return {
        key: payload[key]
        for key in (
            "id",
            "target_namespace",
            "target_vocabulary_id",
            "target_concept_id",
            "target_code",
            "target_grain",
            "target_role",
            "method",
            "rank",
            "score",
            "confidence",
        )
    }


def _evidence_payload(evidence: MappingEvidence) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "candidate_id": evidence.candidate_id,
        "decision_id": evidence.decision_id,
        "evidence_key": evidence.evidence_key,
        "evidence_type": evidence.evidence_type,
        "source_reference": evidence.source_reference,
        "method": evidence.method,
        "payload": evidence.payload,
        "created_at": evidence.created_at.isoformat(),
    }


def _decision_payload(decision: MappingDecision) -> dict[str, Any]:
    return {
        "id": decision.id,
        "input_id": decision.input_id,
        "decision_version": decision.decision_version,
        "decision_status": decision.decision_status,
        "outcome_code": decision.outcome_code,
        "reason_codes": decision.reason_codes,
        "decision_origin": decision.decision_origin,
        "decided_by": decision.decided_by,
        "metadata": decision.metadata_,
        "decided_at": decision.decided_at.isoformat(),
    }


def _event_payload(event: MappingDecisionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "decision_id": event.decision_id,
        "event_type": event.event_type,
        "detail": event.detail,
        "created_at": event.created_at.isoformat(),
    }
