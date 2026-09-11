"""OMOP CDM lookups used by Groundstore review consumers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from typing import Any

import sqlalchemy as sa
from oa_configurator import ResolvedCDMDatabase
from omop_alchemy.cdm.model import ConceptRow
from omop_alchemy.cdm.model.vocabulary import ConceptView, Drug_Strength
from omop_alchemy.cdm.query import ConceptFilter
from omop_alchemy.config import create_cdm_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .enrichment import enrich_candidate_targets


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
        """Attach generic OMOP concept metadata to review candidates."""
        copied_items = [dict(item) for item in items]
        concept_ids = [
            str(candidate["target_concept_id"])
            for item in copied_items
            for candidate in item.get("candidates", [])
            if candidate.get("target_concept_id") is not None
        ]
        concepts = self.lookup(concept_ids)
        for item in copied_items:
            item["candidates"] = [
                {
                    **candidate,
                    "cdm_concept": concepts[str(candidate["target_concept_id"])],
                }
                if str(candidate.get("target_concept_id")) in concepts
                else dict(candidate)
                for candidate in item.get("candidates", [])
            ]
        return copied_items

    def close(self) -> None:
        """Release the engine created for this lookup."""
        self.engine.dispose()


class DrugStrengthLookup:
    """Optional OMOP ``drug_strength`` lookup and candidate enricher."""

    namespace = "omop.drug_strength"

    def __init__(
        self,
        engine: Engine,
        *,
        concept_lookup: CdmConceptLookup | None = None,
    ) -> None:
        self.engine = engine
        self._concept_lookup = concept_lookup or CdmConceptLookup(engine)

    @classmethod
    def from_database(cls, database: ResolvedCDMDatabase) -> DrugStrengthLookup:
        """Create an optional drug-strength lookup from a resolved CDM resource."""
        engine = create_cdm_engine(database)
        return cls(engine, concept_lookup=CdmConceptLookup(engine))

    def lookup(
        self, drug_concept_ids: Iterable[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Return active strength rows grouped by drug concept ID."""
        numeric_ids = tuple(
            dict.fromkeys(
                numeric_id
                for raw_id in drug_concept_ids
                if (numeric_id := _concept_id(raw_id)) is not None
            )
        )
        if not numeric_ids:
            return {}

        statement = (
            sa.select(Drug_Strength)
            .where(
                Drug_Strength.drug_concept_id.in_(numeric_ids),
                Drug_Strength.is_valid_expr(),
            )
            .order_by(
                Drug_Strength.drug_concept_id,
                Drug_Strength.ingredient_concept_id,
            )
        )
        with Session(self.engine) as session:
            strengths = session.scalars(statement).all()
        grouped = {str(concept_id): [] for concept_id in numeric_ids}
        for strength in strengths:
            grouped[str(strength.drug_concept_id)].append(_strength_payload(strength))
        return {concept_id: rows for concept_id, rows in grouped.items() if rows}

    def enrich_items(
        self, items: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Optionally attach strength rows and referenced concept metadata."""
        copied_items = [dict(item) for item in items]
        concept_ids = [
            str(candidate["target_concept_id"])
            for item in copied_items
            for candidate in item.get("candidates", [])
            if candidate.get("target_concept_id") is not None
        ]
        strengths = self.lookup(concept_ids)
        strength_concept_ids = [
            str(concept_id)
            for rows in strengths.values()
            for strength in rows
            for concept_id in (
                strength["ingredient_concept_id"],
                strength["amount_unit_concept_id"],
                strength["numerator_unit_concept_id"],
                strength["denominator_unit_concept_id"],
            )
            if concept_id is not None
        ]
        concepts = self._concept_lookup.lookup(strength_concept_ids)
        return enrich_candidate_targets(
            copied_items,
            namespace=self.namespace,
            enrichments={
                concept_id: [
                    _enrich_strength(strength, concepts) for strength in rows
                ]
                for concept_id, rows in strengths.items()
            },
        )

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


def _strength_payload(strength: Drug_Strength) -> dict[str, Any]:
    return {
        "drug_concept_id": strength.drug_concept_id,
        "ingredient_concept_id": strength.ingredient_concept_id,
        "amount_value": strength.amount_value,
        "amount_unit_concept_id": strength.amount_unit_concept_id,
        "numerator_value": strength.numerator_value,
        "numerator_unit_concept_id": strength.numerator_unit_concept_id,
        "denominator_value": strength.denominator_value,
        "denominator_unit_concept_id": strength.denominator_unit_concept_id,
        "box_size": strength.box_size,
        "valid_start_date": strength.valid_start_date.isoformat(),
        "valid_end_date": strength.valid_end_date.isoformat(),
    }


def _enrich_strength(
    strength: dict[str, Any], concepts: Mapping[str, dict[str, Any]]
) -> dict[str, Any]:
    enriched = dict(strength)
    for field, output in (
        ("ingredient_concept_id", "ingredient_concept"),
        ("amount_unit_concept_id", "amount_unit_concept"),
        ("numerator_unit_concept_id", "numerator_unit_concept"),
        ("denominator_unit_concept_id", "denominator_unit_concept"),
    ):
        concept_id = strength[field]
        if concept_id is not None and str(concept_id) in concepts:
            enriched[output] = concepts[str(concept_id)]
    return enriched
