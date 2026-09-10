from groundstore import (
    DecisionStatus,
    LifecycleStatus,
    MappingCandidateSpec,
    MappingDecisionSpec,
    MappingEvidenceSpec,
    MappingInputSpec,
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
    assert store.latest_decision(input_record.id).id == decision.id

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
    }


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

    store.update_run(run.id, lifecycle_status=LifecycleStatus.COMPLETE.value, last_error=None)
    assert store.latest_successful_run("eviq_hemonc", target_system="omop").id == run.id
