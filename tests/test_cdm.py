from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

pytest.importorskip("omop_alchemy")

from omop_alchemy.cdm.model.vocabulary import Concept, Concept_Class, Domain, Vocabulary
from orm_loader.helpers import Base

from groundstore.cdm import CdmConceptLookup


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
