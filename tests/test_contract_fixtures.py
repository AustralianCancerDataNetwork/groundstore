import json
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mapping_contract_cases.json"


def test_contract_fixtures_cover_edge_cases() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "contract-v1"
    cases = payload["cases"]
    assert {case["case_id"] for case in cases} == {
        "pbs_exact_one_to_one",
        "pbs_combination_one_to_many",
        "eviq_component_only_review",
        "eviq_redirected_incomplete",
        "unmappable_input",
        "failed_run_resume",
    }

    for case in cases:
        assert case["source_namespace"] in {"pbs", "eviq_hemonc"}
        assert case["lifecycle_status"] in {
            "complete",
            "incomplete",
            "failed",
        }
        assert case["decision_status"] in {
            None,
            "mapped",
            "ambiguous",
            "unmappable",
            "needs_review",
        }
        assert case["outcome_code"].startswith(("pbs.", "eviq."))

        candidates = {candidate["candidate_id"] for candidate in case["candidates"]}
        selected = set(case["selected_candidate_ids"])
        evidence = {item["evidence_id"] for item in case["evidence"]}

        assert selected <= candidates
        assert all(
            evidence_id in evidence
            for candidate in case["candidates"]
            for evidence_id in candidate["evidence_ids"]
        )


def test_contract_fixture_includes_multiplicity_and_failure_resume() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in payload["cases"]}

    combination = cases["pbs_combination_one_to_many"]
    assert len(combination["selected_candidate_ids"]) == 2

    failed = cases["failed_run_resume"]
    assert failed["lifecycle_status"] == "failed"
    assert failed["decision_status"] is None
    assert failed["input"]["retryable"] is True
