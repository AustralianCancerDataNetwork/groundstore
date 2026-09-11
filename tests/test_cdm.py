from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

pytest.importorskip("omop_alchemy")

from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Class,
    Domain,
    Drug_Strength,
    Vocabulary,
)
from orm_loader.helpers import Base

from groundstore.cdm import CdmConceptLookup, DrugStrengthLookup


def test_cdm_concept_lookup_uses_typed_omop_concept_models() -> None:
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    Domain(domain_id="Drug", domain_name="Drug", domain_concept_id=0),
                    Vocabulary(
                        vocabulary_id="RxNorm",
                        vocabulary_name="RxNorm",
                        vocabulary_reference="RxNorm",
                        vocabulary_version="test",
                        vocabulary_concept_id=0,
                    ),
                    Concept_Class(
                        concept_class_id="Ingredient",
                        concept_class_name="Ingredient",
                        concept_class_concept_id=0,
                    ),
                    Concept(
                        concept_id=123,
                        concept_name="Example drug",
                        domain_id="Drug",
                        vocabulary_id="RxNorm",
                        concept_class_id="Ingredient",
                        standard_concept="S",
                        concept_code="EXAMPLE",
                        valid_start_date=date(1970, 1, 1),
                        valid_end_date=date(2099, 12, 31),
                    ),
                ]
            )
            session.commit()

        lookup = CdmConceptLookup(engine)
        assert lookup.lookup(("123", "not-an-id"))["123"] == {
            "concept_id": 123,
            "concept_name": "Example drug",
            "concept_code": "EXAMPLE",
            "domain_id": "Drug",
            "vocabulary_id": "RxNorm",
            "concept_class_id": "Ingredient",
            "standard_concept": "S",
            "is_standard": True,
            "is_valid": True,
        }
    finally:
        engine.dispose()


def test_drug_strength_lookup_is_optional_and_namespaced() -> None:
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    Domain(domain_id="Drug", domain_name="Drug", domain_concept_id=0),
                    Domain(domain_id="Unit", domain_name="Unit", domain_concept_id=1),
                    Vocabulary(
                        vocabulary_id="RxNorm",
                        vocabulary_name="RxNorm",
                        vocabulary_reference="RxNorm",
                        vocabulary_version="test",
                        vocabulary_concept_id=0,
                    ),
                    Concept_Class(
                        concept_class_id="Clinical Drug",
                        concept_class_name="Clinical Drug",
                        concept_class_concept_id=0,
                    ),
                    Concept_Class(
                        concept_class_id="Ingredient",
                        concept_class_name="Ingredient",
                        concept_class_concept_id=0,
                    ),
                    Concept_Class(
                        concept_class_id="Unit",
                        concept_class_name="Unit",
                        concept_class_concept_id=0,
                    ),
                    Concept(
                        concept_id=100,
                        concept_name="Example 200 MG tablet",
                        domain_id="Drug",
                        vocabulary_id="RxNorm",
                        concept_class_id="Clinical Drug",
                        standard_concept="S",
                        concept_code="EXAMPLE-200",
                        valid_start_date=date(1970, 1, 1),
                        valid_end_date=date(2099, 12, 31),
                    ),
                    Concept(
                        concept_id=200,
                        concept_name="Example ingredient",
                        domain_id="Drug",
                        vocabulary_id="RxNorm",
                        concept_class_id="Ingredient",
                        standard_concept="S",
                        concept_code="EXAMPLE",
                        valid_start_date=date(1970, 1, 1),
                        valid_end_date=date(2099, 12, 31),
                    ),
                    Concept(
                        concept_id=300,
                        concept_name="milligram",
                        domain_id="Unit",
                        vocabulary_id="RxNorm",
                        concept_class_id="Unit",
                        standard_concept="S",
                        concept_code="mg",
                        valid_start_date=date(1970, 1, 1),
                        valid_end_date=date(2099, 12, 31),
                    ),
                    Drug_Strength(
                        drug_concept_id=100,
                        ingredient_concept_id=200,
                        amount_value=200,
                        amount_unit_concept_id=300,
                        valid_start_date=date(1970, 1, 1),
                        valid_end_date=date(2099, 12, 31),
                    ),
                ]
            )
            session.commit()

        concept_lookup = CdmConceptLookup(engine)
        strength_lookup = DrugStrengthLookup(engine, concept_lookup=concept_lookup)
        strengths = strength_lookup.lookup(("100", "200", "not-an-id"))
        assert strengths["100"][0]["ingredient_concept_id"] == 200
        assert strengths["100"][0]["amount_value"] == 200
        generic_enriched = concept_lookup.enrich_items(
            [{"candidates": [{"target_concept_id": "100"}]}]
        )
        assert "target_enrichments" not in generic_enriched[0]["candidates"][0]
        enriched = strength_lookup.enrich_items(
            [
                {
                    "candidates": [{"target_concept_id": "100"}],
                }
            ]
        )
        strength = enriched[0]["candidates"][0]["target_enrichments"][
            "omop.drug_strength"
        ][0]
        assert strength["ingredient_concept"]["concept_name"] == "Example ingredient"
        assert strength["amount_unit_concept"]["concept_name"] == "milligram"
    finally:
        engine.dispose()
