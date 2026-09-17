"""Repository-style persistence API for mapping workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Literal, Self, cast
from uuid import uuid4

from oa_configurator import ResolvedDatabase, Resolver
from pydantic import ValidationError
from sqlalchemy import Engine, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, sessionmaker

from .contracts import (
    DecisionOrigin,
    MappingCandidateSpec,
    MappingDecisionSpec,
    MappingEvidenceSpec,
    MappingInputSpec,
    MappingOverrideImportResult,
    MappingOverrideSpec,
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
    MappingOverride,
    MappingRun,
)


class _Unset:
    """Sentinel for distinguishing omitted values from explicit nulls."""


_UNSET = _Unset()


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

    def close(self) -> None:
        """Release the SQLAlchemy engine owned by this store."""
        self.engine.dispose()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

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
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
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
                        raise
            return run

    def update_run(
        self,
        run_id: str,
        *,
        lifecycle_status: str | None = None,
        last_error: str | None | _Unset = _UNSET,
    ) -> MappingRun:
        with self._session_factory() as session:
            run = session.get(MappingRun, run_id)
            if run is None:
                raise KeyError(f"unknown mapping run: {run_id}")
            if lifecycle_status is not None:
                run.lifecycle_status = lifecycle_status
            if last_error is not _UNSET:
                run.last_error = cast(str | None, last_error)
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
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
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
                        raise
            return record

    def update_input(
        self,
        input_id: str,
        *,
        lifecycle_status: str | None = None,
        retry_count: int | None = None,
        last_error: str | None | _Unset = _UNSET,
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
            if last_error is not _UNSET:
                record.last_error = cast(str | None, last_error)
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
                    .order_by(
                        MappingCandidate.rank,
                        MappingCandidate.created_at,
                        MappingCandidate.id,
                    )
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
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    candidate = session.scalar(
                        select(MappingCandidate).where(
                            MappingCandidate.input_id == input_id,
                            MappingCandidate.candidate_key == key,
                        )
                    )
                    if candidate is None:
                        raise
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
            if evidence is not None and (
                evidence.candidate_id != candidate_id
                or evidence.decision_id != decision_id
            ):
                raise ValueError(
                    f"evidence key {spec.evidence_key!r} is already attached "
                    "to a different candidate or decision"
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
            input_record = session.scalar(
                select(MappingInput)
                .where(MappingInput.id == input_id)
                .with_for_update()
            )
            if input_record is None:
                raise KeyError(f"unknown mapping input: {input_id}")
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
            decision_origin = spec.decision_origin or (
                DecisionOrigin.OPERATOR_REVIEW
                if "operator_review" in spec.reason_codes or spec.decided_by is not None
                else DecisionOrigin.ALGORITHM
            )
            decision = MappingDecision(
                id=_id(),
                input_id=input_id,
                decision_version=version,
                decision_status=spec.decision_status.value,
                decision_origin=decision_origin.value,
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
                        "decision_origin": decision_origin.value,
                    },
                    created_at=_now(),
                )
            )
            session.add(decision)
            session.commit()
            return decision

    def upsert_override(self, spec: MappingOverrideSpec) -> MappingOverride:
        """Create or replace the active override for one source identity atomically."""
        with self._session_factory() as session:
            override, _outcome = self._upsert_override_in_session(session, spec)
            session.commit()
            return override

    def get_override(
        self,
        source_namespace: str,
        source_kind: str,
        source_identity: str,
        target_system: str,
    ) -> MappingOverride | None:
        """Return the active override for an exact source/target identity."""
        with self._session_factory() as session:
            return session.scalar(
                select(MappingOverride).where(
                    MappingOverride.source_namespace == source_namespace,
                    MappingOverride.source_kind == source_kind,
                    MappingOverride.source_identity == source_identity,
                    MappingOverride.target_system == target_system,
                    MappingOverride.retired_at.is_(None),
                )
            )

    def find_overrides(
        self,
        source_namespace: str,
        *,
        include_retired: bool = False,
    ) -> list[MappingOverride]:
        """List overrides in deterministic source order."""
        with self._session_factory() as session:
            query = select(MappingOverride).where(
                MappingOverride.source_namespace == source_namespace
            )
            if not include_retired:
                query = query.where(MappingOverride.retired_at.is_(None))
            query = query.order_by(
                MappingOverride.source_kind,
                MappingOverride.source_identity,
                MappingOverride.id,
            )
            return list(session.scalars(query))

    def retire_override(
        self,
        override_id: str,
        *,
        reason: str,
        retired_by: str,
    ) -> MappingOverride:
        """Retire an override while retaining its audit history."""
        if not reason.strip() or not retired_by.strip():
            raise ValueError("retirement reason and retired_by are required")
        with self._session_factory() as session:
            override = session.get(MappingOverride, override_id)
            if override is None:
                raise KeyError(f"unknown mapping override: {override_id}")
            if override.retired_at is None:
                override.retired_at = _now()
                override.retired_by = retired_by
                override.retirement_reason = reason
                session.commit()
            return override

    def import_overrides(
        self,
        specs: Sequence[MappingOverrideSpec | Mapping[str, Any]],
    ) -> MappingOverrideImportResult:
        """Import a batch atomically, rejecting duplicate identities as one unit."""
        rows: list[MappingOverrideSpec] = []
        rejected: list[dict[str, Any]] = []
        for index, raw in enumerate(specs):
            try:
                rows.append(
                    raw
                    if isinstance(raw, MappingOverrideSpec)
                    else MappingOverrideSpec.model_validate(raw)
                )
            except ValidationError as exc:
                rejected.append(
                    {
                        "row": index,
                        "reason": "invalid_override",
                        "detail": exc.errors(include_url=False),
                    }
                )
        if rejected:
            return MappingOverrideImportResult(rejected=rejected)

        seen: set[tuple[str, str, str, str]] = set()
        for index, spec in enumerate(rows):
            identity = (
                spec.source_namespace,
                spec.source_kind,
                spec.source_identity,
                spec.target_system,
            )
            if identity in seen:
                rejected.append({"row": index, "reason": "duplicate_source_identity"})
            seen.add(identity)
        if rejected:
            return MappingOverrideImportResult(rejected=rejected)

        with self._session_factory() as session:
            created = 0
            replaced = 0
            unchanged = 0
            for spec in rows:
                _override, outcome = self._upsert_override_in_session(session, spec)
                if outcome == "replaced":
                    replaced += 1
                elif outcome == "created":
                    created += 1
                else:
                    unchanged += 1
            session.commit()
            return MappingOverrideImportResult(
                created=created,
                replaced=replaced,
                unchanged=unchanged,
            )

    def _upsert_override_in_session(
        self,
        session: Session,
        spec: MappingOverrideSpec,
    ) -> tuple[MappingOverride, Literal["created", "replaced", "unchanged"]]:
        """Apply one override using the caller's transaction."""
        identity_filter = (
            MappingOverride.source_namespace == spec.source_namespace,
            MappingOverride.source_kind == spec.source_kind,
            MappingOverride.source_identity == spec.source_identity,
            MappingOverride.target_system == spec.target_system,
            MappingOverride.retired_at.is_(None),
        )
        current = session.scalar(select(MappingOverride).where(*identity_filter).with_for_update())
        if current is not None:
            same = (
                current.decision_status == spec.decision_status.value
                and current.target_reference == spec.target_reference
                and current.confirmed_fingerprint == spec.confirmed_fingerprint
                and current.rationale == spec.rationale
                and current.authored_by == spec.authored_by
            )
            if same:
                return current, "unchanged"
            current.retired_at = _now()
            current.retired_by = spec.authored_by
            current.retirement_reason = "replaced_by_new_override"
            session.flush()
        override = MappingOverride(
            id=_id(),
            source_namespace=spec.source_namespace,
            source_kind=spec.source_kind,
            source_identity=spec.source_identity,
            target_system=spec.target_system,
            decision_status=spec.decision_status.value,
            target_reference=spec.target_reference,
            confirmed_fingerprint=spec.confirmed_fingerprint,
            rationale=spec.rationale,
            authored_by=spec.authored_by,
            authored_at=_now(),
        )
        session.add(override)
        return override, "replaced" if current is not None else "created"

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
            lifecycle_rows = session.execute(
                select(MappingInput.lifecycle_status, func.count(MappingInput.id))
                .where(MappingInput.run_id == run_id)
                .group_by(MappingInput.lifecycle_status)
            )
            lifecycle_status = {status: int(count) for status, count in lifecycle_rows}
            latest_versions = (
                select(
                    MappingDecision.input_id,
                    func.max(MappingDecision.decision_version).label("decision_version"),
                )
                .join(MappingInput, MappingInput.id == MappingDecision.input_id)
                .where(MappingInput.run_id == run_id)
                .group_by(MappingDecision.input_id)
                .subquery()
            )
            decision_rows = session.execute(
                select(MappingDecision.decision_status, func.count(MappingDecision.id))
                .join(
                    latest_versions,
                    and_(
                        latest_versions.c.input_id == MappingDecision.input_id,
                        latest_versions.c.decision_version
                        == MappingDecision.decision_version,
                    ),
                )
                .group_by(MappingDecision.decision_status)
            )
            origin_expression = func.coalesce(
                MappingDecision.decision_origin,
                DecisionOrigin.ALGORITHM.value,
            )
            origin_rows = session.execute(
                select(
                    origin_expression,
                    func.count(MappingDecision.id),
                )
                .join(
                    latest_versions,
                    and_(
                        latest_versions.c.input_id == MappingDecision.input_id,
                        latest_versions.c.decision_version
                        == MappingDecision.decision_version,
                    ),
                )
                .group_by(origin_expression)
            )
            return {
                "input_count": sum(lifecycle_status.values()),
                "lifecycle_status": lifecycle_status,
                "decision_status": {
                    status: int(count) for status, count in decision_rows
                },
                "decision_origin": {
                    origin: int(count) for origin, count in origin_rows
                },
            }

    def get_run(self, run_id: str) -> MappingRun | None:
        with self._session_factory() as session:
            return session.get(MappingRun, run_id)

    def run_summary(self, run_id: str) -> dict[str, Any]:
        """Return run metadata and efficient lifecycle/decision counts."""
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"unknown mapping run: {run_id}")
        coverage = self.coverage(run_id)
        return {
            "schema_version": "groundstore.mapping-run-summary.v1",
            "run_id": run.id,
            "source_namespace": run.source_namespace,
            "source_fingerprint": run.source_fingerprint,
            "source_snapshot": run.source_snapshot,
            "target_system": run.target_system,
            "target_release": run.target_release,
            "algorithm_version": run.algorithm_version,
            "policy_version": run.policy_version,
            "run_lifecycle_status": run.lifecycle_status,
            "last_error": run.last_error,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "input_count": coverage["input_count"],
            "lifecycle_counts": coverage["lifecycle_status"],
            "decision_counts": coverage["decision_status"],
            "decision_origin_counts": coverage["decision_origin"],
        }

    def progress(self, run_id: str) -> dict[str, Any]:
        """Return queue and processing counts for one mapping run."""
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"unknown mapping run: {run_id}")
        coverage = self.coverage(run_id)
        lifecycle = coverage["lifecycle_status"]
        pending = lifecycle.get("pending", 0)
        in_progress = lifecycle.get("in_progress", 0)
        complete = lifecycle.get("complete", 0)
        failed = lifecycle.get("failed", 0)
        incomplete = lifecycle.get("incomplete", 0)
        latest_input_update = None
        with self._session_factory() as session:
            latest_input_update = session.scalar(
                select(func.max(MappingInput.updated_at)).where(
                    MappingInput.run_id == run_id
                )
            )
        last_updated = (
            max(run.updated_at, latest_input_update)
            if latest_input_update
            else run.updated_at
        )
        return {
            "schema_version": "groundstore.mapping-progress.v1",
            "run_id": run.id,
            "run_status": run.lifecycle_status,
            "total_inputs": coverage["input_count"],
            "queued_count": pending,
            "active_count": in_progress,
            "complete_count": complete,
            "retryable_count": failed,
            "blocked_count": incomplete,
            "remaining_count": coverage["input_count"] - complete,
            "decision_counts": coverage["decision_status"],
            "decision_origin_counts": coverage["decision_origin"],
            "override_count": coverage["decision_origin"].get(
                DecisionOrigin.MANUAL_OVERRIDE.value, 0
            ),
            "last_updated_at": last_updated.isoformat(),
        }

    def find_runs(
        self,
        *,
        algorithm_version: str | None = None,
        run_ids: Sequence[str] | None = None,
        source_namespace: str | None = None,
        target_system: str | None = None,
        policy_version: str | None = None,
    ) -> list[MappingRun]:
        """Find runs using exact, intentionally narrow cleanup filters."""
        with self._session_factory() as session:
            query = select(MappingRun).order_by(MappingRun.created_at, MappingRun.id)
            if algorithm_version is not None:
                query = query.where(MappingRun.algorithm_version == algorithm_version)
            if run_ids is not None:
                if not run_ids:
                    return []
                query = query.where(MappingRun.id.in_(run_ids))
            if source_namespace is not None:
                query = query.where(MappingRun.source_namespace == source_namespace)
            if target_system is not None:
                query = query.where(MappingRun.target_system == target_system)
            if policy_version is not None:
                query = query.where(MappingRun.policy_version == policy_version)
            return list(session.scalars(query))

    def delete_runs(self, run_ids: Sequence[str]) -> int:
        """Delete exactly the identified runs and their dependent records."""
        if not run_ids:
            return 0
        with self._session_factory() as session:
            runs = list(
                session.scalars(select(MappingRun).where(MappingRun.id.in_(run_ids)))
            )
            for run in runs:
                session.delete(run)
            session.commit()
            return len(runs)

    def get_inputs(self, run_id: str) -> list[MappingInput]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(MappingInput)
                    .where(MappingInput.run_id == run_id)
                    .order_by(
                        MappingInput.source_kind,
                        MappingInput.source_key,
                        MappingInput.id,
                    )
                )
            )

    def get_inputs_page(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        lifecycle_status: str | None = None,
        decision_status: str | None = None,
    ) -> tuple[list[tuple[MappingInput, str]], int]:
        """Return a paginated input view with each input's latest status."""
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
            base = (
                select(MappingInput, decision_status_expression)
                .select_from(MappingInput)
                .outerjoin(latest_versions, latest_versions.c.input_id == MappingInput.id)
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
            count_query = (
                select(func.count(MappingInput.id))
                .select_from(MappingInput)
                .outerjoin(latest_versions, latest_versions.c.input_id == MappingInput.id)
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
            if lifecycle_status is not None:
                base = base.where(MappingInput.lifecycle_status == lifecycle_status)
                count_query = count_query.where(
                    MappingInput.lifecycle_status == lifecycle_status
                )
            if decision_status is not None:
                base = base.where(decision_status_expression == decision_status)
                count_query = count_query.where(decision_status_expression == decision_status)
            rows = list(
                session.execute(
                    base.order_by(
                        MappingInput.source_kind,
                        MappingInput.source_key,
                        MappingInput.id,
                    )
                    .offset(offset)
                    .limit(limit)
                )
            )
            total = int(session.scalar(count_query) or 0)
            return [(input_record, str(status)) for input_record, status in rows], total

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
        rows, total = self.get_inputs_page(
            run_id,
            offset=offset,
            limit=limit,
            decision_status=decision_status,
        )
        input_ids = [input_record.id for input_record, _ in rows]
        with self._session_factory() as session:
            candidates_by_input: dict[str, list[MappingCandidate]] = {
                input_id: [] for input_id in input_ids
            }
            if input_ids:
                candidates = session.scalars(
                    select(MappingCandidate)
                    .where(MappingCandidate.input_id.in_(input_ids))
                    .order_by(
                        MappingCandidate.input_id,
                        MappingCandidate.rank,
                        MappingCandidate.created_at,
                        MappingCandidate.id,
                    )
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
            return session.scalar(
                query.order_by(MappingRun.updated_at.desc(), MappingRun.id.desc())
            )


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
