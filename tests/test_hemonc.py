from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

pytest.importorskip("hemonc_alchemy")

from hemonc_alchemy.model import (
    Base,
    Indications,
    Regimens,
    Sigs,
    indications_Regimen_cuiMap,
)
from hemonc_alchemy.model.enums import (
    Indications_RegulatorEnum,
    Regimens_Highest_evidenceEnum,
    Regimens_Regimen_typeEnum,
    Sigs_Component_roleEnum,
    Sigs_FrequencyEnum,
    Sigs_RouteEnum,
)

from groundstore.hemonc import HemoncRegimenLookup


def test_hemonc_regimen_lookup_enriches_components_and_indications() -> None:
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        captured_at = datetime(2026, 1, 1, tzinfo=UTC)
        with Session(engine) as session:
            session.add(
                Regimens(
                    id=1,
                    regimen_cui=7001,
                    regimen_name="Example regimen",
                    regimen_type=Regimens_Regimen_typeEnum.COMPONENT_TO_BASED_REGIMEN,
                    highest_evidence=Regimens_Highest_evidenceEnum.EXPERT_OPINION,
                    contains_rt=False,
                    sact=True,
                    studies=0,
                    variantcount=1,
                    variantcountdate=captured_at,
                    date_added=captured_at,
                )
            )
            session.add(
                Sigs(
                    id=2,
                    component="Example drug",
                    component_cui=8001,
                    component_role=Sigs_Component_roleEnum.PRIMARY_SYSTEMIC,
                    date_added=captured_at,
                    divided=False,
                    phase_step=1,
                    portion="induction",
                    regimen="Example regimen",
                    regimen_cui=7001,
                    step_number="1 of 1 (default)",
                    variant="Example regimen variant",
                    variant_cui=9001,
                    doseminnum="100",
                    doseunit="mg",
                    frequency=Sigs_FrequencyEnum.ONCE_PER_DAY,
                    route=Sigs_RouteEnum.ORAL,
                )
            )
            indication = Indications(
                id=3,
                accelerated=False,
                component="Example drug",
                component_cui=8001,
                condition="Example cancer",
                condition_cui=6001,
                date_added=captured_at,
                first_in_class=False,
                regulator=Indications_RegulatorEnum.FDA,
                withdrawn="no",
                biomarker="Example biomarker",
                biomarker_finding="positive",
                stage="advanced",
            )
            session.add(indication)
            session.flush()
            session.add(
                indications_Regimen_cuiMap(
                    parent_id=indication.id,
                    regimen_cui=7001,
                )
            )
            session.commit()

        lookup = HemoncRegimenLookup(engine)
        payload = lookup.lookup(("7001", "not-a-cui"))["7001"]
        assert payload["regimen"]["regimen_name"] == "Example regimen"
        assert payload["components"][0]["component_cui"] == 8001
        assert payload["components"][0]["sigs"][0]["route"] == "oral"
        assert payload["indications"][0]["condition"] == "Example cancer"
        assert payload["indications"][0]["biomarker_finding"] == "positive"

        enriched = lookup.enrich_items(
            [
                {
                    "candidates": [
                        {
                            "target_namespace": "hemonc",
                            "target_code": "7001",
                        },
                        {
                            "target_namespace": "omop",
                            "target_concept_id": "7001",
                        },
                    ]
                }
            ]
        )
        assert enriched[0]["candidates"][0]["target_enrichments"][
            "hemonc.regimen"
        ]["regimen"]["regimen_cui"] == 7001
        assert "target_enrichments" not in enriched[0]["candidates"][1]
    finally:
        engine.dispose()
