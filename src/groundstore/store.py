"""Repository-style persistence API for mapping workflows."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from oa_configurator import ResolvedDatabase, Resolver
from sqlalchemy import Engine, and_, func, select
from sqlalchemy.orm import aliased, sessionmaker

from .contracts import (
    MappingCandidateSpec,
    MappingDecisionSpec,
    MappingEvidenceSpec,
    MappingInputSpec,
    MappingRunSpec,
)
from .engine import create_groundstore_engine, create_schema
from .models import (
    MappingCandidate,
    MappingDecision,
    MappingDecisionCandidate,
    MappingDecisionEvent,
    MappingEvidence,
    MappingInput,
    MappingRun,
)


class MappingStore:
    """Persist and resume source-independent mapping workflows."""

    def __init__(self, engine: Engine, *, initialize: bool = True) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        if initialize:
            create_schema(engine)

    @classmethod
    def from_url(cls, url: str, **engine_kwargs: Any) -> MappingStore:
        return cls(create_groundstore_engine(url, **engine_kwargs))

    @classmethod
    def from_database(cls, database: ResolvedDatabase, **engine_kwargs: Any) -> MappingStore:
        """Create a store from an already-resolved OA database resource."""
        return cls(create_groundstore_engine(resolved_database=database, **engine_kwargs))

    @classmethod
    def from_resolver(
        cls, resolver: Resolver, database_name: str = "mapping_db", **engine_kwargs: Any
    ) -> MappingStore:
        engine = create_groundstore_engine(
            resolver=resolver, database_name=database_name, **engine_kwargs
        )
        return cls(engine)

    def get_or_create_run(self, spec: MappingRunSpec) -> MappingRun:
        """Return the stable run for *spec*, preserving resumable state."""
        with self._session_factory() as session:
            run = session.scalar(
                select(MappingRun).where(
                    MappingRun.source_namespace == spec.source_namespace,
                    MappingRun.source_fingerprint == spec.source_fingerprint,
                    MappingRun.target_system == spec.target_system,
                    MappingRun.target_release == spec.target_release,
                    MappingRun.algorithm_version == spec.algorithm_version,
                    MappingRun.policy_version == spec.policy_version,
                )
            )
            if run is None:
                now = _now()
                run = MappingRun(
                    id=_id(),
                    **spec.model_dump(exclude={"lifecycle_status"}),
                    lifecycle_status=spec.lifecycle_status.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(run)
                session.commit()
            return run

    def update_run(
        self,
        run_id: str,
        *,
        lifecycle_status: str | None = None,
        last_error: str | None = None,
    ) -> MappingRun:
        with self._session_factory() as session:
            run = session.get(MappingRun, run_id)
            if run is None:
                raise KeyError(f"unknown mapping run: {run_id}")
            if lifecycle_status is not None:
                run.lifecycle_status = lifecycle_status
            run.last_error = last_error
            run.updated_at = _now()
            session.commit()
            return run

    def upsert_input(self, run_id: str, spec: MappingInputSpec) -> MappingInput:
        """Insert or return an input, retaining candidates and decisions on retry."""
        with self._session_factory() as session:
            record = session.scalar(
                select(MappingInput).where(
                    MappingInput.run_id == run_id,
                    MappingInput.source_namespace == spec.source_namespace,
                    MappingInput.source_kind == spec.source_kind,
                    MappingInput.source_key == spec.source_key,
                    MappingInput.source_fingerprint == spec.source_fingerprint,
                )
            )
            if record is None:
                now = _now()
                record = MappingInput(
                    id=_id(),
                    run_id=run_id,
                    **spec.model_dump(exclude={"lifecycle_status"}),
                    lifecycle_status=spec.lifecycle_status.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.commit()
            return record

    def update_input(
        self,
        input_id: str,
        *,
        lifecycle_status: str | None = None,
        retry_count: int | None = None,
        last_error: str | None = None,
    ) -> MappingInput:
        """Update processing state without replacing mapping evidence."""
        with self._session_factory() as session:
            record = session.get(MappingInput, input_id)
            if record is None:
                raise KeyError(f"unknown mapping input: {input_id}")
            if lifecycle_status is not None:
                record.lifecycle_status = lifecycle_status
            if retry_count is not None:
                if retry_count < 0:
                    raise ValueError("retry_count cannot be negative")
                record.retry_count = retry_count
            record.last_error = last_error
            record.updated_at = _now()
            session.commit()
            return record

    def get_candidates(self, input_id: str) -> list[MappingCandidate]:
        """Return candidates for one input in deterministic rank order."""
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(MappingCandidate)
                    .where(MappingCandidate.input_id == input_id)
                    .order_by(MappingCandidate.rank, MappingCandidate.created_at)
                )
            )

    def upsert_candidate(self, input_id: str, spec: MappingCandidateSpec) -> MappingCandidate:
        """Insert or return a candidate using a deterministic semantic key."""
        key = _candidate_key(spec)
        with self._session_factory() as session:
            candidate = session.scalar(
                select(MappingCandidate).where(
                    MappingCandidate.input_id == input_id,
                    MappingCandidate.candidate_key == key,
                )
            )
            if candidate is None:
                candidate = MappingCandidate(
                    id=_id(),
                    input_id=input_id,
                    candidate_key=key,
                    **spec.model_dump(exclude={"metadata"}),
                    metadata_=spec.metadata,
                    created_at=_now(),
                )
                session.add(candidate)
                session.commit()
            return candidate

    def add_evidence(
        self,
        input_id: str,
        spec: MappingEvidenceSpec,
        *,
        candidate_id: str | None = None,
        decision_id: str | None = None,
    ) -> MappingEvidence:
        """Insert or return evidence, attached to exactly one candidate or decision."""
        if (candidate_id is None) == (decision_id is None):
            raise ValueError("evidence must reference exactly one candidate or decision")
        with self._session_factory() as session:
            if candidate_id is not None:
                candidate = session.scalar(
                    select(MappingCandidate).where(
                        MappingCandidate.id == candidate_id,
                        MappingCandidate.input_id == input_id,
                    )
                )
                if candidate is None:
                    raise ValueError("candidate must belong to the input")
            if decision_id is not None:
                decision = session.scalar(
                    select(MappingDecision).where(
                        MappingDecision.id == decision_id,
                        MappingDecision.input_id == input_id,
                    )
                )
                if decision is None:
                    raise ValueError("decision must belong to the input")
            evidence = session.scalar(
                select(MappingEvidence).where(
                    MappingEvidence.input_id == input_id,
                    MappingEvidence.evidence_key == spec.evidence_key,
                )
            )
            if evidence is None:
                evidence = MappingEvidence(
                    id=_id(),
                    input_id=input_id,
                    candidate_id=candidate_id,
                    decision_id=decision_id,
                    **spec.model_dump(),
                    created_at=_now(),
                )
                session.add(evidence)
                session.commit()
            return evidence

    def record_decision(self, input_id: str, spec: MappingDecisionSpec) -> MappingDecision:
        """Append a decision version and its immutable history event."""
        with self._session_factory() as session:
            if len(spec.selected_candidate_ids) != len(set(spec.selected_candidate_ids)):
                raise ValueError("selected candidates must be unique")
            selected = list(
                session.scalars(
                    select(MappingCandidate).where(
                        MappingCandidate.input_id == input_id,
                        MappingCandidate.id.in_(spec.selected_candidate_ids),
                    )
                )
            )
            if len(selected) != len(set(spec.selected_candidate_ids)):
                raise ValueError("all selected candidates must belong to the input")
            latest = session.scalar(
                select(MappingDecision)
                .where(MappingDecision.input_id == input_id)
                .order_by(MappingDecision.decision_version.desc())
            )
            version = 1 if latest is None else latest.decision_version + 1
            decision = MappingDecision(
                id=_id(),
                input_id=input_id,
                decision_version=version,
                decision_status=spec.decision_status.value,
                outcome_code=spec.outcome_code,
                reason_codes=spec.reason_codes,
                decided_by=spec.decided_by,
                metadata_=spec.metadata,
                decided_at=_now(),
            )
            decision.selected_candidates = selected
            decision.history.append(
                MappingDecisionEvent(
                    id=_id(),
                    event_type="decision_recorded",
                    detail={
                        "decision_status": spec.decision_status.value,
                        "decision_version": version,
                    },
                    created_at=_now(),
                )
            )
            session.add(decision)
            session.commit()
            return decision

    def latest_decision(self, input_id: str) -> MappingDecision | None:
        with self._session_factory() as session:
            return session.scalar(
                select(MappingDecision)
                .where(MappingDecision.input_id == input_id)
                .order_by(MappingDecision.decision_version.desc())
            )

    def get_input(self, input_id: str) -> MappingInput | None:
        """Return one input without relying on lazy relationships."""
        with self._session_factory() as session:
            return session.get(MappingInput, input_id)

    def get_evidence(self, input_id: str) -> list[MappingEvidence]:
        """Return all evidence for an input in insertion order."""
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(MappingEvidence)
                    .where(MappingEvidence.input_id == input_id)
                    .order_by(MappingEvidence.created_at, MappingEvidence.id)
                )
            )

    def get_decision_candidate_ids(self, decision_id: str) -> list[str]:
        """Return selected candidate IDs in stable candidate order."""
        with self._session_factory() as session:
            rows = session.execute(
                select(MappingDecisionCandidate.candidate_id)
                .where(MappingDecisionCandidate.decision_id == decision_id)
                .join(
                    MappingCandidate,
                    MappingCandidate.id == MappingDecisionCandidate.candidate_id,
                )
                .order_by(MappingCandidate.rank, MappingCandidate.id)
            )
            return [candidate_id for (candidate_id,) in rows]

    def get_decision_history(self, decision_id: str) -> list[MappingDecisionEvent]:
        """Return immutable history events for one decision."""
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(MappingDecisionEvent)
                    .where(MappingDecisionEvent.decision_id == decision_id)
                    .order_by(MappingDecisionEvent.created_at, MappingDecisionEvent.id)
                )
            )

    def coverage(self, run_id: str) -> dict[str, Any]:
        """Return lifecycle and latest decision counts for a run."""
        with self._session_factory() as session:
            inputs = list(
                session.scalars(select(MappingInput).where(MappingInput.run_id == run_id))
            )
            decisions = []
            for input_record in inputs:
                decision = session.scalar(
                    select(MappingDecision)
                    .where(MappingDecision.input_id == input_record.id)
                    .order_by(MappingDecision.decision_version.desc())
                )
                if decision is not None:
                    decisions.append(decision)
            return {
                "input_count": len(inputs),
                "lifecycle_status": dict(Counter(item.lifecycle_status for item in inputs)),
                "decision_status": dict(Counter(item.decision_status for item in decisions)),
            }

    def get_run(self, run_id: str) -> MappingRun | None:
        with self._session_factory() as session:
            return session.get(MappingRun, run_id)

    def get_inputs(self, run_id: str) -> list[MappingInput]:
        with self._session_factory() as session:
            return list(session.scalars(select(MappingInput).where(MappingInput.run_id == run_id)))

    def get_review_inputs(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
        decision_status: str | None = None,
    ) -> tuple[list[tuple[MappingInput, str, list[MappingCandidate]]], int]:
        """Return one stable, database-paginated review page.

        The synthetic ``pending`` status represents an input without a
        decision.  Other statuses are source-independent strings so adapters
        may add meaningful outcomes without changing Groundstore's contract.
        """
        if offset < 0:
            raise ValueError("offset cannot be negative")
        if limit < 1:
            raise ValueError("limit must be positive")

        latest_versions = (
            select(
                MappingDecision.input_id,
                func.max(MappingDecision.decision_version).label("decision_version"),
            )
            .group_by(MappingDecision.input_id)
            .subquery()
        )
        latest_decision = aliased(MappingDecision)
        decision_status_expression = func.coalesce(latest_decision.decision_status, "pending")
        with self._session_factory() as session:
            count_query = (
                select(func.count(MappingInput.id))
                .select_from(MappingInput)
                .outerjoin(
                    latest_versions,
                    latest_versions.c.input_id == MappingInput.id,
                )
                .outerjoin(
                    latest_decision,
                    and_(
                        latest_decision.input_id == MappingInput.id,
                        latest_decision.decision_version
                        == latest_versions.c.decision_version,
                    ),
                )
                .where(MappingInput.run_id == run_id)
            )
            page_query = (
                select(MappingInput, decision_status_expression)
                .select_from(MappingInput)
                .outerjoin(
                    latest_versions,
                    latest_versions.c.input_id == MappingInput.id,
                )
                .outerjoin(
                    latest_decision,
                    and_(
                        latest_decision.input_id == MappingInput.id,
                        latest_decision.decision_version
                        == latest_versions.c.decision_version,
                    ),
                )
                .where(MappingInput.run_id == run_id)
                .order_by(
                    MappingInput.source_kind,
                    MappingInput.source_key,
                    MappingInput.id,
                )
                .offset(offset)
                .limit(limit)
            )
            if decision_status is not None:
                count_query = count_query.where(decision_status_expression == decision_status)
                page_query = page_query.where(decision_status_expression == decision_status)

            total = int(session.scalar(count_query) or 0)
            rows = list(session.execute(page_query))
            input_ids = [input_record.id for input_record, _ in rows]
            candidates_by_input: dict[str, list[MappingCandidate]] = {
                input_id: [] for input_id in input_ids
            }
            if input_ids:
                candidates = session.scalars(
                    select(MappingCandidate)
                    .where(MappingCandidate.input_id.in_(input_ids))
                    .order_by(MappingCandidate.input_id, MappingCandidate.rank)
                )
                for candidate in candidates:
                    candidates_by_input[candidate.input_id].append(candidate)

            return [
                (
                    input_record,
                    str(status),
                    candidates_by_input[input_record.id],
                )
                for input_record, status in rows
            ], total

    def latest_successful_run(
        self, source_namespace: str, *, target_system: str | None = None
    ) -> MappingRun | None:
        """Return the newest complete run for a source and target system."""
        with self._session_factory() as session:
            query = select(MappingRun).where(
                MappingRun.source_namespace == source_namespace,
                MappingRun.lifecycle_status == "complete",
            )
            if target_system is not None:
                query = query.where(MappingRun.target_system == target_system)
            return session.scalar(query.order_by(MappingRun.updated_at.desc()))


def _id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _candidate_key(spec: MappingCandidateSpec) -> str:
    payload = {
        "target_namespace": spec.target_namespace,
        "target_vocabulary_id": spec.target_vocabulary_id,
        "target_concept_id": spec.target_concept_id,
        "target_code": spec.target_code,
        "target_grain": spec.target_grain,
        "target_role": spec.target_role,
        "method": spec.method,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
