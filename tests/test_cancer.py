from datetime import date
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

pytest.importorskip("omop_alchemy")

from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Relationship,
    Concept_Synonym,
    Domain,
    Vocabulary,
)
from orm_loader.helpers import Base

from groundstore.cancer import (
    CancerConditionLookup,
    CancerConditionPolicy,
    CancerConditionResolver,
)


@pytest.mark.parametrize("factory", [CancerConditionLookup, CancerConditionResolver])
def test_cancer_factories_preserve_nondefault_policy_options(
    factory, monkeypatch, omop_engine
) -> None:
    monkeypatch.setattr("groundstore.cancer._create_vocabulary_engine", lambda _: omop_engine)
    lookup = factory.from_database(
        None,
        cancer_parent_concept_ids=iter((902, 900, 902)),
        vocabulary_ids=iter(("SNOMED CT", "SNOMED CT")),
        include_synonyms=False,
        require_standard=False,
        include_classification=False,
        require_active=True,
        include_non_standard_descendants=True,
        unknown_concept_id=999,
    )

    assert lookup.policy == CancerConditionPolicy(
        cancer_parent_concept_ids=(900, 902),
        vocabulary_ids=("SNOMED CT",),
        include_synonyms=False,
        require_standard=False,
        include_classification=False,
        require_active=True,
        include_non_standard_descendants=True,
        unknown_concept_id=999,
    )
    assert lookup.engine is omop_engine
    assert lookup.resolve(None) == 999


@pytest.mark.parametrize("factory", [CancerConditionLookup, CancerConditionResolver])
def test_cancer_factories_validate_scope_before_opening_database(factory, monkeypatch) -> None:
    create_engine = Mock(side_effect=AssertionError("Database must not open"))
    monkeypatch.setattr("groundstore.cancer._create_vocabulary_engine", create_engine)

    with pytest.raises(ValueError, match="cancer_parent_concept_ids"):
        factory.from_database(None, cancer_parent_concept_ids=())

    create_engine.assert_not_called()


def _concept(
    concept_id: int,
    name: str,
    *,
    code: str,
    domain: str = "Condition",
    standard: str | None = "S",
) -> Concept:
    return Concept(
        concept_id=concept_id,
        concept_name=name,
        domain_id=domain,
        vocabulary_id="SNOMED CT",
        concept_class_id="Clinical Disease",
        standard_concept=standard,
        concept_code=code,
        valid_start_date=date(1970, 1, 1),
        valid_end_date=date(2099, 12, 31),
    )


def test_cancer_condition_lookup_resolves_scope_and_enriches_candidates(omop_engine) -> None:
    engine = omop_engine
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Domain(
                    domain_id="Condition",
                    domain_name="Condition",
                    domain_concept_id=0,
                ),
                Vocabulary(
                    vocabulary_id="SNOMED CT",
                    vocabulary_name="SNOMED CT",
                    vocabulary_reference="test",
                    vocabulary_version="test",
                    vocabulary_concept_id=0,
                ),
                _concept(900, "Malignant neoplastic disease", code="CANCER"),
                _concept(901, "Lung adenocarcinoma", code="LUNG-ADENO"),
                _concept(903, "Pulmonary adenocarcinoma", code="LUNG-ADENO-2"),
                _concept(902, "Asthma", code="ASTHMA"),
                Concept_Synonym(
                    concept_id=901,
                    concept_synonym_name="Adenocarcinoma of lung",
                    language_concept_id=0,
                ),
                Concept_Synonym(
                    concept_id=903,
                    concept_synonym_name="Adenocarcinoma of lung",
                    language_concept_id=0,
                ),
                Concept_Ancestor(
                    ancestor_concept_id=900,
                    descendant_concept_id=900,
                    min_levels_of_separation=0,
                    max_levels_of_separation=0,
                ),
                Concept_Ancestor(
                    ancestor_concept_id=900,
                    descendant_concept_id=901,
                    min_levels_of_separation=1,
                    max_levels_of_separation=1,
                ),
                Concept_Ancestor(
                    ancestor_concept_id=900,
                    descendant_concept_id=903,
                    min_levels_of_separation=1,
                    max_levels_of_separation=1,
                ),
                Concept_Relationship(
                    concept_id_1=901,
                    concept_id_2=901,
                    relationship_id="Maps to",
                    valid_start_date=date(1970, 1, 1),
                    valid_end_date=date(2099, 12, 31),
                    invalid_reason=None,
                ),
            ]
        )
        session.commit()

    lookup = CancerConditionLookup(
        engine,
        CancerConditionPolicy(
            cancer_parent_concept_ids=(900,),
            vocabulary_ids=("SNOMED CT",),
        ),
    )

    assert lookup.resolve("adenocarcinoma of lung") is None
    assert lookup.resolve_candidates("adenocarcinoma of lung") == (901, 903)
    assert lookup.resolve("Asthma") is None
    context = lookup.lookup(("901", "902", "not-an-id"))
    assert context["901"]["concept"]["concept_name"] == "Lung adenocarcinoma"
    assert context["901"]["cancer_scope"]["matched"] is True
    assert context["901"]["maps_to"][0]["standard_concept_id"] == 901
    assert context["902"]["cancer_scope"]["matched"] is False

    enriched = lookup.enrich_items(
        [
            {
                "candidates": [
                    {"target_concept_id": "901"},
                    {"target_concept_id": "902"},
                ]
            }
        ]
    )
    payload = enriched[0]["candidates"][0]["target_enrichments"][
        "omop.cancer_condition"
    ]
    assert payload["cancer_scope"]["matched"] is True
    assert (
        enriched[0]["candidates"][1]["target_enrichments"][
            "omop.cancer_condition"
        ]["cancer_scope"]["matched"]
        is False
    )


def test_cancer_condition_policy_requires_an_explicit_scope() -> None:
    with pytest.raises(ValueError, match="cancer_parent_concept_ids"):
        CancerConditionPolicy(cancer_parent_concept_ids=())
