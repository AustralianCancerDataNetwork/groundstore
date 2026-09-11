"""OMOP-specific cancer-condition resolution and candidate enrichment."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from threading import RLock
from typing import Any

import sqlalchemy as sa
from oa_configurator import ResolvedCDMDatabase
from omop_alchemy.cdm.model.vocabulary import Concept
from omop_alchemy.cdm.query import ConceptFilter
from omop_alchemy.config import create_cdm_engine
from omop_alchemy.toolkit.core.concepts import (
    OMOPConceptSource,
    RuntimeConceptSetSpec,
    StandardConceptMappingSpec,
    normalize_default,
    runtime_concept_predicate,
    standard_concept_mapping_select,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.orm import Session

from .cdm import CdmConceptLookup
from .enrichment import enrich_candidate_targets


@dataclass(frozen=True, slots=True)
class CancerConditionPolicy:
    """Declarative policy for a reusable cancer-condition lookup.

    Parent concepts are expanded through ``concept_ancestor`` by omop-alchemy.
    OMOP vocabulary loads normally include self-ancestor rows; enrichment
    scope membership additionally includes configured parents explicitly so it
    does not depend on that convention.
    """

    cancer_parent_concept_ids: tuple[int, ...]
    vocabulary_ids: tuple[str, ...] = ()
    include_synonyms: bool = True
    require_standard: bool = True
    include_classification: bool = True
    require_active: bool = False
    include_non_standard_descendants: bool = False
    unknown_concept_id: int | None = None

    def __post_init__(self) -> None:
        parent_ids = tuple(sorted(set(self.cancer_parent_concept_ids)))
        if not parent_ids or any(concept_id <= 0 for concept_id in parent_ids):
            raise ValueError(
                "cancer_parent_concept_ids must contain at least one positive OMOP concept ID"
            )
        object.__setattr__(self, "cancer_parent_concept_ids", parent_ids)
        object.__setattr__(self, "vocabulary_ids", tuple(dict.fromkeys(self.vocabulary_ids)))


class CancerConditionResolver:
    """Resolve condition text to concepts in an explicit cancer scope."""

    def __init__(self, engine: Engine, policy: CancerConditionPolicy) -> None:
        self.engine = engine
        self.policy = policy
        self._index: dict[str, tuple[int, ...]] | None = None
        self._resolver_lock = RLock()

    @classmethod
    def from_database(
        cls,
        database: ResolvedCDMDatabase,
        *,
        cancer_parent_concept_ids: Iterable[int],
        vocabulary_ids: Iterable[str] = (),
        include_synonyms: bool = True,
        require_standard: bool = True,
        include_classification: bool = True,
        require_active: bool = False,
        include_non_standard_descendants: bool = False,
        unknown_concept_id: int | None = None,
    ) -> CancerConditionResolver:
        """Create a resolver from an already-resolved OMOP database resource."""
        return cls(
            create_cdm_engine(database),
            CancerConditionPolicy(
                cancer_parent_concept_ids=tuple(cancer_parent_concept_ids),
                vocabulary_ids=tuple(vocabulary_ids),
                include_synonyms=include_synonyms,
                require_standard=require_standard,
                include_classification=include_classification,
                require_active=require_active,
                include_non_standard_descendants=include_non_standard_descendants,
                unknown_concept_id=unknown_concept_id,
            ),
        )

    def resolve(self, term: str | None) -> int | None:
        """Resolve an unambiguous condition term to an OMOP concept ID."""
        candidates = self.resolve_candidates(term)
        if len(candidates) == 1:
            return candidates[0]
        return self.policy.unknown_concept_id

    def resolve_candidates(self, term: str | None) -> tuple[int, ...]:
        """Return all matching concept IDs without silently resolving collisions."""
        if not term:
            return ()
        return self._get_index().get(normalize_default(term), ())

    def resolve_many(self, terms: Iterable[str | None]) -> dict[str, int | None]:
        """Resolve distinct terms while preserving each supplied text value."""
        resolved: dict[str, int | None] = {}
        for term in terms:
            key = "" if term is None else term
            if key not in resolved:
                resolved[key] = self.resolve(term)
        return resolved

    def _get_index(self) -> dict[str, tuple[int, ...]]:
        if self._index is None:
            with self._resolver_lock:
                if self._index is None:
                    with Session(self.engine) as session:
                        rows = OMOPConceptSource.fetch_concepts(
                            session,
                            domain_id="Condition",
                            vocabulary_id=list(self.policy.vocabulary_ids) or None,
                            require_standard=self.policy.require_standard,
                            include_classification=self.policy.include_classification,
                            require_active=self.policy.require_active,
                            parents=list(self.policy.cancer_parent_concept_ids),
                            include_non_standard_descendants=(
                                self.policy.include_non_standard_descendants
                            ),
                        )
                        concept_ids = {row.concept_id for row in rows}
                        keys: defaultdict[str, set[int]] = defaultdict(set)
                        for row in rows:
                            for value in (row.concept_name, row.concept_code):
                                if value:
                                    keys[normalize_default(value)].add(row.concept_id)
                        if self.policy.include_synonyms:
                            for concept_id, synonym in OMOPConceptSource.fetch_synonyms(
                                session, concept_ids=concept_ids
                            ):
                                keys[normalize_default(synonym)].add(concept_id)
                        self._index = {
                            key: tuple(sorted(concept_ids))
                            for key, concept_ids in keys.items()
                        }
        return self._index

    def close(self) -> None:
        """Release the engine created for this resolver."""
        self.engine.dispose()


class CancerConditionLookup:
    """Resolve and enrich OMOP cancer-condition mapping candidates.

    Candidate enrichment is opt-in and adds a namespaced
    ``omop.cancer_condition`` payload with concept metadata, cancer-scope
    membership, and valid standard mappings.
    """

    namespace = "omop.cancer_condition"

    def __init__(
        self,
        engine: Engine,
        policy: CancerConditionPolicy,
        *,
        concept_lookup: CdmConceptLookup | None = None,
        resolver: CancerConditionResolver | None = None,
    ) -> None:
        self.engine = engine
        self.policy = policy
        self._concept_lookup = concept_lookup or CdmConceptLookup(engine)
        self._resolver = resolver or CancerConditionResolver(engine, policy)

    @classmethod
    def from_database(
        cls,
        database: ResolvedCDMDatabase,
        *,
        cancer_parent_concept_ids: Iterable[int],
        vocabulary_ids: Iterable[str] = (),
        include_synonyms: bool = True,
        require_standard: bool = True,
        include_classification: bool = True,
        require_active: bool = False,
        include_non_standard_descendants: bool = False,
        unknown_concept_id: int | None = None,
    ) -> CancerConditionLookup:
        """Create a lookup from an already-resolved OMOP database resource."""
        engine = create_cdm_engine(database)
        policy = CancerConditionPolicy(
            cancer_parent_concept_ids=tuple(cancer_parent_concept_ids),
            vocabulary_ids=tuple(vocabulary_ids),
            include_synonyms=include_synonyms,
            require_standard=require_standard,
            include_classification=include_classification,
            require_active=require_active,
            include_non_standard_descendants=include_non_standard_descendants,
            unknown_concept_id=unknown_concept_id,
        )
        return cls(engine, policy)

    def resolve(self, term: str | None) -> int | None:
        """Resolve one source condition term to an OMOP concept ID."""
        return self._resolver.resolve(term)

    def resolve_many(self, terms: Iterable[str | None]) -> dict[str, int | None]:
        """Resolve distinct source condition terms."""
        return self._resolver.resolve_many(terms)

    def resolve_candidates(self, term: str | None) -> tuple[int, ...]:
        """Return all matching condition concepts for an input term."""
        return self._resolver.resolve_candidates(term)

    def lookup(self, concept_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return cancer-condition context for known OMOP concept IDs."""
        numeric_ids = tuple(
            dict.fromkeys(
                numeric_id
                for raw_id in concept_ids
                if (numeric_id := _concept_id(raw_id)) is not None
            )
        )
        if not numeric_ids:
            return {}

        concepts = self._concept_lookup.lookup(
            tuple(str(concept_id) for concept_id in numeric_ids)
        )
        concepts = {
            concept_id: concept
            for concept_id, concept in concepts.items()
            if concept["domain_id"] == "Condition"
        }
        condition_ids = tuple(int(concept_id) for concept_id in concepts)
        if not condition_ids:
            return {}
        scoped_ids = self._scoped_ids(condition_ids)
        mappings = self._standard_mappings(condition_ids)
        return {
            concept_id: {
                "concept": concept,
                "cancer_scope": {
                    "matched": int(concept_id) in scoped_ids,
                    "parent_concept_ids": list(self.policy.cancer_parent_concept_ids),
                },
                "maps_to": mappings.get(int(concept_id), []),
            }
            for concept_id, concept in concepts.items()
        }

    def enrich_items(
        self, items: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach optional cancer-condition context to candidate copies."""
        copied_items = [dict(item) for item in items]
        concept_ids = [
            str(candidate["target_concept_id"])
            for item in copied_items
            for candidate in item.get("candidates", [])
            if candidate.get("target_concept_id") is not None
        ]
        return enrich_candidate_targets(
            copied_items,
            namespace=self.namespace,
            enrichments=self.lookup(concept_ids),
        )

    def close(self) -> None:
        """Release the engine created for this lookup."""
        self.engine.dispose()

    def _scoped_ids(self, concept_ids: Sequence[int]) -> set[int]:
        with Session(self.engine) as session:
            scope = RuntimeConceptSetSpec(
                include_ancestor_ids=self.policy.cancer_parent_concept_ids,
                include_exact_ids=self.policy.cancer_parent_concept_ids,
                require_standard=self.policy.require_standard,
                include_classification=self.policy.include_classification,
            )
            statement = sa.select(Concept.concept_id).where(
                Concept.concept_id.in_(concept_ids),
                runtime_concept_predicate(Concept.concept_id, scope),
            )
            statement = ConceptFilter(
                domains=("Condition",),
                vocabularies=tuple(self.policy.vocabulary_ids) or None,
                require_standard=self.policy.require_standard,
                include_classification=self.policy.include_classification,
                require_active=self.policy.require_active,
            ).apply(statement)
            return set(session.scalars(statement).all())

    def _standard_mappings(
        self, concept_ids: Sequence[int]
    ) -> dict[int, list[dict[str, Any]]]:
        grouped: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
        with Session(self.engine) as session:
            statement = standard_concept_mapping_select(
                StandardConceptMappingSpec(source_concept_ids=tuple(concept_ids))
            )
            rows = session.execute(statement).mappings().all()
        rows = sorted(
            rows,
            key=lambda row: (
                int(row["source_concept_id"]),
                int(row["standard_concept_id"]),
            ),
        )
        for row in rows:
            source_id = int(row["source_concept_id"])
            grouped[source_id].append(_standard_mapping_payload(row))
        return dict(grouped)


def _concept_id(raw_id: str) -> int | None:
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _standard_mapping_payload(row: RowMapping) -> dict[str, Any]:
    """Serialize one toolkit mapping row at the JSON boundary."""
    return _StandardMappingPayload.model_validate(row).model_dump(mode="json")


class _StandardMappingPayload(BaseModel):
    """Stable JSON representation of an OMOP standard mapping row."""

    model_config = ConfigDict(extra="ignore")

    source_concept_id: int
    source_vocabulary_id: str
    source_concept_code: str
    source_concept_name: str
    standard_concept_id: int
    standard_vocabulary_id: str
    standard_concept_code: str
    standard_concept_name: str
    relationship_valid_start_date: date | datetime
    relationship_valid_end_date: date | datetime
