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
    assert payload["decision"]["decision_origin"] == "operator_review"
    assert payload["decision"]["history"][0]["event_type"] == "decision_recorded"
    assert context.status("pbs", target_system="omop")["coverage"]["input_count"] == 1
    summary = context.run_summary(run.id)
    assert summary.input_count == 1
    assert summary.lifecycle_counts == {"pending": 1}
    assert summary.decision_counts == {"mapped": 1}
    assert summary.decision_origin_counts == {"operator_review": 1}
    progress = context.progress(run.id)
    assert progress.total_inputs == 1
    assert progress.complete_count == 0
    assert progress.queued_count == 1
    assert progress.remaining_count == 1
    assert progress.decision_counts == {"mapped": 1}
    assert progress.override_count == 0

    handoff = context.review_handoff(
        input_record.id, requested_by="groundworkers", metadata={"queue": "pbs"}
    )
    assert handoff.task_id == f"mapping-review:{input_record.id}"
    assert handoff.source_namespace == "pbs"
    assert handoff.metadata == {"queue": "pbs"}
    assert handoff.model_dump(mode="json")["packet"] == payload


def test_read_context_paginates_review_inputs_and_filters_latest_status(store):
    run = store.get_or_create_run(
        MappingRunSpec(
            source_namespace="pbs",
            source_fingerprint="snapshot-page",
            target_system="omop",
            algorithm_version="mapper-1",
            policy_version="policy-1",
        )
    )
    for key in ("B", "A", "C"):
        input_record = store.upsert_input(
            run.id,
            MappingInputSpec(
                source_namespace="pbs",
                source_kind="drug",
                source_key=key,
                source_fingerprint=f"input-{key}",
                normalized_projection={"name": key},
            ),
        )
        candidate = store.upsert_candidate(
            input_record.id,
            MappingCandidateSpec(
                target_namespace="omop",
                target_vocabulary_id="RxNorm",
                target_concept_id=key,
                target_grain="ingredient",
                method="exact",
                rank=1,
            ),
        )
        store.record_decision(
            input_record.id,
            MappingDecisionSpec(
                decision_status=(
                    DecisionStatus.AMBIGUOUS if key != "B" else DecisionStatus.MAPPED
                ),
                selected_candidate_ids=[candidate.id],
                outcome_code="pbs.test",
            ),
        )
    store.upsert_input(
        run.id,
        MappingInputSpec(
            source_namespace="pbs",
            source_kind="drug",
            source_key="D",
            source_fingerprint="input-D",
            normalized_projection={"name": "D"},
        ),
    )
    store.update_run(run.id, lifecycle_status="complete")

    context = MappingReadContext(store)
    page = context.review_page(
        "pbs", run_id=run.id, page=1, page_size=1, decision_status="ambiguous"
    )
    payload = page.model_dump(mode="json")

    assert payload["schema_version"] == "groundstore.mapping-review-page.v1"
    assert payload["total_items"] == 2
    assert payload["page_count"] == 2
    assert payload["items"][0]["source_key"] == "A"
    assert payload["items"][0]["candidate_count"] == 1
    assert payload["items"][0]["decision_status"] == "ambiguous"

    pending = context.review_page("pbs", run_id=run.id, decision_status="pending")
    assert pending.total_items == 1
    assert pending.items[0]["source_key"] == "D"
