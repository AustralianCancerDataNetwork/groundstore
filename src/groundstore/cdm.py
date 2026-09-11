"""OMOP CDM lookups used by Groundstore review consumers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from typing import Any

import sqlalchemy as sa
from oa_configurator import ResolvedCDMDatabase
from omop_alchemy.cdm.model import ConceptRow
from omop_alchemy.cdm.model.vocabulary import ConceptView
from omop_alchemy.cdm.query import ConceptFilter
from omop_alchemy.config import create_cdm_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


class CdmConceptLookup:
    """Resolve OMOP concept metadata through omop-alchemy's query surface."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_database(cls, database: ResolvedCDMDatabase) -> CdmConceptLookup:
        """Create a lookup from an already-resolved CDM database resource."""
        return cls(create_cdm_engine(database))

    def lookup(self, concept_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return display metadata for the supplied OMOP concept IDs."""
        numeric_ids = tuple(
            dict.fromkeys(
                numeric_id
                for raw_id in concept_ids
                if (numeric_id := _concept_id(raw_id)) is not None
            )
        )
        if not numeric_ids:
            return {}

        statement = ConceptFilter(concept_ids=numeric_ids).apply(sa.select(ConceptView))
        with Session(self.engine) as session:
            concepts = session.scalars(statement.order_by(ConceptView.concept_id)).all()
        return {str(concept.concept_id): _concept_payload(concept) for concept in concepts}

    def enrich_items(
        self, items: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach CDM metadata to review candidates without changing Groundstore contracts."""
        copied_items = [dict(item) for item in items]
        concept_ids = [
            str(candidate["target_concept_id"])
            for item in copied_items
            for candidate in item.get("candidates", [])
            if candidate.get("target_concept_id") is not None
        ]
        concepts = self.lookup(concept_ids)
        for item in copied_items:
            enriched_candidates = []
            for candidate in item.get("candidates", []):
                enriched = dict(candidate)
                concept = concepts.get(str(candidate.get("target_concept_id")))
                if concept is not None:
                    enriched["cdm_concept"] = concept
                enriched_candidates.append(enriched)
            item["candidates"] = enriched_candidates
        return copied_items

    def close(self) -> None:
        """Release the engine created for this lookup."""
        self.engine.dispose()


def _concept_id(raw_id: str) -> int | None:
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _concept_payload(concept: ConceptView) -> dict[str, Any]:
    payload = asdict(
        ConceptRow(
            concept_id=concept.concept_id,
            concept_name=concept.concept_name,
            concept_code=concept.concept_code,
            domain_id=concept.domain_id,
            concept_class_id=concept.concept_class_id,
            vocabulary_id=concept.vocabulary_id,
            standard_concept=concept.standard_concept,
        )
    )
    payload.update(is_standard=concept.is_standard, is_valid=concept.is_valid)
    return payload
