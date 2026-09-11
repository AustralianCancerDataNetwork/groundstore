import pytest
from textual.widgets import TextArea

pytest.importorskip("groundskeeping")

from groundstore import (
    DecisionStatus,
    MappingCandidateSpec,
    MappingDecisionSpec,
    MappingInputSpec,
    MappingRunSpec,
)
from groundstore.tui import _summary_detail, create_mapping_review_app


class _ConceptLookup:
    def enrich_items(self, items):
        enriched = []
        for item in items:
            copied = dict(item)
            copied["candidates"] = [
                {
                    **candidate,
                    "cdm_concept": {
                        "concept_name": "Example drug",
                        "concept_class_id": "Ingredient",
                    },
                }
                for candidate in item["candidates"]
            ]
            enriched.append(copied)
        return enriched


def _seed_review(store):
    run = store.get_or_create_run(
        MappingRunSpec(
            source_namespace="pbs",
            source_fingerprint="tui-review",
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
            normalized_projection={"drug_name": "fluorouracil"},
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
    store.record_decision(
        input_record.id,
        MappingDecisionSpec(
            decision_status=DecisionStatus.AMBIGUOUS,
            selected_candidate_ids=[candidate.id],
        ),
    )
    store.update_run(run.id, lifecycle_status="complete")


def test_mapping_review_app_renders_groundstore_page(store):
    _seed_review(store)
    app = create_mapping_review_app(
        store,
        source_namespace="pbs",
        concept_lookup=_ConceptLookup(),
        page_size=1,
    )

    async def run():
        async with app.run_test() as pilot:
            row_key = app._workbench.rows_table.ordered_rows[0].key.value
            assert row_key is not None
            row = app._workbench.rows_table.get_row(row_key)
            assert "fluorouracil" in str(row[1])
            assert "Example drug" in str(row[3])
            assert "123" not in " ".join(str(value) for value in row)
            assert "Page 1 of 1" in str(
                app._workbench.query_one("#result-panel").border_subtitle
            )
            app._workbench.rows_table.focus()
            await pilot.press("enter")
            await pilot.pause()
            context = app.query_one("#context", expect_type=TextArea)
            assert "groundstore.mapping-review-handoff.v1" in context.text
            await pilot.press("q")

    import asyncio

    asyncio.run(run())


def test_mapping_review_detail_keeps_supporting_source_metadata(store):
    detail = _summary_detail(
        {
            "input_id": "input-1",
            "source_kind": "drug",
            "source_key": "opaque-source-key",
            "decision_status": "ambiguous",
            "lifecycle_status": "complete",
            "normalized_projection": {"drug_name": "fluorouracil"},
            "candidates": [],
        }
    )
    rendered = " ".join(f"{key} {value}" for key, value in detail.rows)
    assert "opaque-source-key" in rendered
    assert '"drug_name": "fluorouracil"' in rendered
