from groundstore import (
    DecisionStatus,
    MappingCandidateSpec,
    MappingDecisionSpec,
    MappingEvidenceSpec,
    MappingInputSpec,
    MappingReadContext,
    MappingRunSpec,
)


def test_read_context_builds_json_safe_packet_and_review_handoff(store):
    run = store.get_or_create_run(
        MappingRunSpec(
            source_namespace="pbs",
            source_fingerprint="snapshot-1",
            target_system="omop",
            algorithm_version="mapper-1",
            policy_version="policy-1",
        )
    )
    input_record = store.upsert_input(
        run.id,
        MappingInputSpec(
            source_namespace="pbs",
            source_kind="drug",
            source_key="123",
            source_fingerprint="input-123",
            normalized_projection={"name": "fluorouracil"},
        ),
    )
    candidate = store.upsert_candidate(
        input_record.id,
        MappingCandidateSpec(
            target_namespace="omop",
            target_vocabulary_id="RxNorm",
            target_concept_id="1001",
            target_grain="ingredient",
            method="exact",
            rank=1,
        ),
    )
    store.add_evidence(
        input_record.id,
        MappingEvidenceSpec(
            evidence_key="source-name",
            evidence_type="source_projection",
            source_reference="pbs:123",
            payload={"matched": True},
        ),
        candidate_id=candidate.id,
    )
    store.record_decision(
        input_record.id,
        MappingDecisionSpec(
            decision_status=DecisionStatus.MAPPED,
            selected_candidate_ids=[candidate.id],
            outcome_code="pbs.exact",
            decided_by="reviewer",
        ),
    )
    store.update_run(run.id, lifecycle_status="complete")

    context = MappingReadContext(store)
    packet = context.evidence_packet(input_record.id)
    payload = packet.model_dump(mode="json")

    assert payload["schema_version"] == "groundstore.mapping-evidence-packet.v1"
    assert payload["input"]["source_key"] == "123"
    assert payload["candidates"][0]["evidence"][0]["evidence_key"] == "source-name"
    assert payload["decision"]["selected_candidate_ids"] == [candidate.id]
    assert payload["decision"]["history"][0]["event_type"] == "decision_recorded"
    assert context.status("pbs", target_system="omop")["coverage"]["input_count"] == 1

    handoff = context.review_handoff(
        input_record.id, requested_by="groundworkers", metadata={"queue": "pbs"}
    )
    assert handoff.task_id == f"mapping-review:{input_record.id}"
    assert handoff.source_namespace == "pbs"
    assert handoff.metadata == {"queue": "pbs"}
    assert handoff.model_dump(mode="json")["packet"] == payload
