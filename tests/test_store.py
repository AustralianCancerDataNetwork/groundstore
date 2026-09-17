import pytest

from groundstore import (
    DecisionStatus,
    LifecycleStatus,
    MappingCandidateSpec,
    MappingDecisionSpec,
    MappingEvidenceSpec,
    MappingInputSpec,
    MappingOverrideSpec,
    MappingRunSpec,
    MappingStore,
)


def _run_spec() -> MappingRunSpec:
    return MappingRunSpec(
        source_namespace="eviq_hemonc",
        source_fingerprint="snapshot-1",
        source_snapshot={"source": "fixture"},
        target_system="omop",
        target_release="hemonc-1",
        algorithm_version="mapper-1",
        policy_version="policy-1",
    )


def test_candidate_requires_a_target_identifier() -> None:
    with pytest.raises(ValueError, match="target_concept_id or target_code"):
        MappingCandidateSpec(target_namespace="omop", method="exact")


def test_store_persists_multiplicity_evidence_and_versioned_decisions(
    store: MappingStore,
) -> None:
    run = store.get_or_create_run(_run_spec())
    input_record = store.upsert_input(
        run.id,
        MappingInputSpec(
            source_namespace="eviq_hemonc",
            source_kind="regimen",
            source_key="123",
            source_fingerprint="regimen-123",
            normalized_projection={"agents": ["fluorouracil", "oxaliplatin"]},
        ),
    )
    first = store.upsert_candidate(
        input_record.id,
        MappingCandidateSpec(
            target_namespace="hemonc",
            target_concept_id="1001",
            target_grain="regimen",
            method="component_match",
            confidence=0.82,
        ),
    )
    second = store.upsert_candidate(
        input_record.id,
        MappingCandidateSpec(
            target_namespace="hemonc",
            target_concept_id="1002",
            target_grain="regimen",
            method="component_match",
            confidence=0.79,
        ),
    )
    assert (
        store.upsert_candidate(
            input_record.id,
            MappingCandidateSpec(
                target_namespace="hemonc",
                target_concept_id="1001",
                target_grain="regimen",
                method="component_match",
                confidence=0.82,
            ),
        ).id
        == first.id
    )

    evidence = store.add_evidence(
        input_record.id,
        MappingEvidenceSpec(
            evidence_key="components",
            evidence_type="normalized_component_set",
            payload={"matched": 2},
        ),
        candidate_id=first.id,
    )
    decision = store.record_decision(
        input_record.id,
        MappingDecisionSpec(
            decision_status=DecisionStatus.AMBIGUOUS,
            selected_candidate_ids=[first.id, second.id],
            outcome_code="eviq.component_match_ambiguous",
            reason_codes=["multiple_schedule_candidates"],
        ),
    )

    assert evidence.candidate_id == first.id
    assert decision.decision_version == 1
    assert len(decision.selected_candidates) == 2
    assert len(decision.history) == 1
    latest = store.latest_decision(input_record.id)
    assert latest is not None
    assert latest.id == decision.id

    with pytest.raises(ValueError, match="different candidate or decision"):
        store.add_evidence(
            input_record.id,
            MappingEvidenceSpec(
                evidence_key="components",
                evidence_type="decision_context",
            ),
            decision_id=decision.id,
        )

    revised = store.record_decision(
        input_record.id,
        MappingDecisionSpec(
            decision_status=DecisionStatus.MAPPED,
            selected_candidate_ids=[first.id],
            outcome_code="eviq.exact_component_match",
        ),
    )
    assert revised.decision_version == 2
    assert store.coverage(run.id) == {
        "input_count": 1,
        "lifecycle_status": {LifecycleStatus.PENDING.value: 1},
        "decision_status": {DecisionStatus.MAPPED.value: 1},
        "decision_origin": {"algorithm": 1},
    }
    page, total = store.get_inputs_page(
        run.id,
        limit=1,
        decision_status=DecisionStatus.MAPPED.value,
    )
    assert total == 1
    assert page[0][0].id == input_record.id
    assert page[0][1] == DecisionStatus.MAPPED.value


def test_store_reuses_run_and_input_after_failure(store: MappingStore) -> None:
    run = store.get_or_create_run(_run_spec())
    input_record = store.upsert_input(
        run.id,
        MappingInputSpec(
            source_namespace="eviq_hemonc",
            source_kind="regimen",
            source_key="456",
            source_fingerprint="regimen-456",
        ),
    )
    store.update_run(run.id, lifecycle_status=LifecycleStatus.FAILED.value, last_error="timeout")
    resumed_run = store.get_or_create_run(_run_spec())
    resumed_input = store.upsert_input(
        resumed_run.id,
        MappingInputSpec(
            source_namespace="eviq_hemonc",
            source_kind="regimen",
            source_key="456",
            source_fingerprint="regimen-456",
        ),
    )

    assert resumed_run.id == run.id
    assert resumed_run.lifecycle_status == LifecycleStatus.FAILED.value
    assert resumed_input.id == input_record.id
    assert len(store.get_inputs(run.id)) == 1

    store.update_input(
        input_record.id,
        lifecycle_status=LifecycleStatus.FAILED.value,
        retry_count=1,
        last_error="source timeout",
    )
    updated = store.get_inputs(run.id)[0]
    assert updated.lifecycle_status == LifecycleStatus.FAILED.value
    assert updated.retry_count == 1
    assert updated.last_error == "source timeout"

    store.update_run(run.id, lifecycle_status=LifecycleStatus.COMPLETE.value)
    current_run = store.get_run(run.id)
    assert current_run is not None
    assert current_run.last_error == "timeout"
    store.update_run(run.id, last_error=None)
    successful = store.latest_successful_run("eviq_hemonc", target_system="omop")
    assert successful is not None
    assert successful.id == run.id


def test_store_deletes_selected_runs_and_dependents(store: MappingStore) -> None:
    run = store.get_or_create_run(_run_spec())
    input_record = store.upsert_input(
        run.id,
        MappingInputSpec(
            source_namespace="eviq_hemonc",
            source_kind="regimen",
            source_key="delete-me",
            source_fingerprint="delete-me",
        ),
    )
    store.upsert_candidate(
        input_record.id,
        MappingCandidateSpec(
            target_namespace="hemonc",
            target_concept_id="1001",
            target_grain="regimen",
            method="component_match",
        ),
    )

    retained_spec = _run_spec().model_copy(update={"algorithm_version": "mapper-2"})
    retained = store.get_or_create_run(retained_spec)

    assert len(store.find_runs(algorithm_version="mapper-1")) == 1
    assert store.delete_runs([run.id]) == 1
    assert store.get_run(run.id) is None
    assert store.get_inputs(run.id) == []
    assert store.get_run(retained.id) is not None


def _override_spec(**updates) -> MappingOverrideSpec:
    values = {
        "source_namespace": "pbs",
        "source_kind": "indication",
        "source_identity": "prescribing-text:123",
        "target_system": "omop",
        "decision_status": DecisionStatus.MAPPED,
        "target_reference": {"vocabulary": "SNOMED", "concept_id": "456"},
        "confirmed_fingerprint": "sha256:abc",
        "rationale": "Confirmed by clinical review",
        "authored_by": "reviewer@example.org",
    }
    values.update(updates)
    return MappingOverrideSpec(**values)


def test_overrides_are_idempotent_and_replacements_are_retained(store: MappingStore) -> None:
    first = store.upsert_override(_override_spec())
    assert store.upsert_override(_override_spec()).id == first.id

    replacement = store.upsert_override(
        _override_spec(
            target_reference={"vocabulary": "SNOMED", "concept_id": "789"},
            rationale="Updated after second clinical review",
        )
    )
    assert replacement.id != first.id
    active = store.get_override("pbs", "indication", "prescribing-text:123", "omop")
    assert active is not None
    assert active.id == replacement.id
    history = store.find_overrides("pbs", include_retired=True)
    assert len(history) == 2
    retired = next(row for row in history if row.retired_at is not None)
    assert retired.retirement_reason == "replaced_by_new_override"


def test_override_import_rejects_duplicate_batch_without_writes(store: MappingStore) -> None:
    spec = _override_spec()
    result = store.import_overrides([spec, spec])
    assert result.created == 0
    assert result.replaced == 0
    assert result.rejected == [{"row": 1, "reason": "duplicate_source_identity"}]
    assert store.find_overrides("pbs") == []


def test_override_import_rejects_invalid_row_without_writes(store: MappingStore) -> None:
    result = store.import_overrides(
        [
            _override_spec(source_identity="prescribing-text:valid"),
            {"source_namespace": "pbs", "decision_status": "mapped"},
        ]
    )
    assert result.created == 0
    assert result.replaced == 0
    assert result.rejected[0]["row"] == 1
    assert store.find_overrides("pbs") == []


def test_override_import_and_retire(store: MappingStore) -> None:
    result = store.import_overrides(
        [
            _override_spec(source_identity="prescribing-text:1"),
            _override_spec(
                source_identity="prescribing-text:2",
                decision_status=DecisionStatus.UNMAPPABLE,
                target_reference=None,
            ),
        ]
    )
    assert result.created == 2
    assert result.rejected == []
    override = store.get_override("pbs", "indication", "prescribing-text:1", "omop")
    assert override is not None
    retired = store.retire_override(
        override.id,
        reason="source withdrawn",
        retired_by="reviewer@example.org",
    )
    assert retired.retired_at is not None
    assert store.get_override("pbs", "indication", "prescribing-text:1", "omop") is None


def test_override_import_reports_identical_rows_as_unchanged(store: MappingStore) -> None:
    spec = _override_spec()
    assert store.import_overrides([spec]).model_dump() == {
        "created": 1,
        "replaced": 0,
        "unchanged": 0,
        "rejected": [],
    }
    assert store.import_overrides([spec]).model_dump() == {
        "created": 0,
        "replaced": 0,
        "unchanged": 1,
        "rejected": [],
    }
