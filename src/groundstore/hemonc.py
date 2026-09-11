"""Optional HemOnc regimen enrichment for mapping review consumers.

This module is deliberately outside Groundstore's core dependency set. Install
the ``groundstore[hemonc]`` extra when a consumer wants to enrich candidate
regimen IDs with the HemOnc model's components and indication context.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from hemonc_alchemy.config import create_hemonc_engine
from hemonc_alchemy.model import (
    Indications,
    Regimens,
    Sigs,
    indications_Regimen_cuiMap,
)
from oa_configurator import ResolvedDatabase
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .enrichment import enrich_candidate_targets


class HemoncRegimenLookup:
    """Optional HemOnc regimen lookup and candidate enricher.

    A lookup returns one namespaced payload per HemOnc regimen CUI. Component
    rows retain sig-level administration context, while indications retain the
    clinical modifiers needed to distinguish otherwise similar regimens. The
    provider is opt-in and only enriches candidates explicitly identified as
    HemOnc targets by namespace or vocabulary.
    """

    namespace = "hemonc.regimen"

    def __init__(
        self,
        engine: Engine,
        *,
        target_namespaces: Sequence[str] = ("hemonc", "hemonc-alchemy"),
        target_vocabulary_ids: Sequence[str] = ("HemOnc",),
    ) -> None:
        self.engine = engine
        self.target_namespaces = frozenset(
            value.strip().lower() for value in target_namespaces if value.strip()
        )
        self.target_vocabulary_ids = frozenset(
            value.strip().lower()
            for value in target_vocabulary_ids
            if value.strip()
        )

    @classmethod
    def from_database(cls, database: ResolvedDatabase, **kwargs: Any) -> HemoncRegimenLookup:
        """Create a lookup from an already-resolved HemOnc database resource."""
        return cls(create_hemonc_engine(database), **kwargs)

    def lookup(self, regimen_cuis: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return regimen metadata, component sigs, and indications by CUI."""
        numeric_ids = tuple(
            dict.fromkeys(
                numeric_id
                for raw_id in regimen_cuis
                if (numeric_id := _regimen_cui(raw_id)) is not None
            )
        )
        if not numeric_ids:
            return {}

        with Session(self.engine) as session:
            regimens = session.scalars(
                sa.select(Regimens)
                .where(Regimens.regimen_cui.in_(numeric_ids))
                .order_by(Regimens.regimen_cui)
            ).all()
            sigs = session.scalars(
                sa.select(Sigs)
                .where(Sigs.regimen_cui.in_(numeric_ids))
                .order_by(
                    Sigs.regimen_cui,
                    Sigs.phase_step,
                    Sigs.step_number,
                    Sigs.component_cui,
                )
            ).all()
            indication_rows = session.execute(
                sa.select(Indications)
                .add_columns(indications_Regimen_cuiMap.regimen_cui)
                .join(
                    indications_Regimen_cuiMap,
                    indications_Regimen_cuiMap.parent_id == Indications.id,
                )
                .where(indications_Regimen_cuiMap.regimen_cui.in_(numeric_ids))
                .order_by(Indications.id)
            ).all()

        components_by_regimen: dict[int, dict[int, _ComponentPayload]] = defaultdict(dict)
        for sig in sigs:
            components = components_by_regimen[sig.regimen_cui]
            component = components.setdefault(
                sig.component_cui,
                _ComponentPayload(
                    component_cui=sig.component_cui,
                    component=sig.component,
                ),
            )
            component.sigs.append(_SigPayload.from_row(sig))

        indications_by_regimen: dict[int, list[_IndicationPayload]] = defaultdict(list)
        for indication, regimen_cui in indication_rows:
            indications_by_regimen[regimen_cui].append(
                _IndicationPayload.from_row(indication)
            )

        payloads: dict[str, dict[str, Any]] = {}
        for regimen in regimens:
            regimen_cui = regimen.regimen_cui
            payloads[str(regimen_cui)] = _RegimenContextPayload(
                regimen=_RegimenPayload.from_row(regimen),
                components=list(components_by_regimen.get(regimen_cui, {}).values()),
                indications=indications_by_regimen.get(regimen_cui, []),
            ).model_dump(mode="json")
        return payloads

    def enrich_items(self, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Attach HemOnc regimen context under ``target_enrichments``."""
        copied_items = [dict(item) for item in items]
        candidate_ids = [
            str(candidate["target_code"])
            for item in copied_items
            for candidate in item.get("candidates", [])
            if self._is_hemonc_target(candidate)
            and candidate.get("target_code") is not None
        ]
        regimens = self.lookup(candidate_ids)
        return enrich_candidate_targets(
            copied_items,
            namespace=self.namespace,
            enrichments=regimens,
            include=self._is_hemonc_target,
            target_field="target_code",
        )

    def close(self) -> None:
        """Release the engine created for this lookup."""
        self.engine.dispose()

    def _is_hemonc_target(self, candidate: Mapping[str, Any]) -> bool:
        namespace = str(candidate.get("target_namespace", "")).strip().lower()
        vocabulary = str(candidate.get("target_vocabulary_id", "")).strip().lower()
        return namespace in self.target_namespaces or vocabulary in self.target_vocabulary_ids


def _regimen_cui(raw_id: str) -> int | None:
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


class _RegimenPayload(BaseModel):
    """JSON representation of a HemOnc regimen row."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    regimen_cui: int
    regimen_name: str
    regimen_type: Any
    sact: bool
    contains_rt: bool
    highest_evidence: Any
    map_ncit: str | None
    components_count: int | None = Field(validation_alias="components")
    variant_count: int = Field(validation_alias="variantcount")

    @classmethod
    def from_row(cls, regimen: Regimens) -> _RegimenPayload:
        return cls.model_validate(regimen.to_dict(include_nulls=True))


class _SigPayload(BaseModel):
    """JSON representation of one HemOnc sig row."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    component_cui: int
    component: str
    component_role: Any
    subcomponent: Any = None
    subcomponent_cui: int | None = None
    portion: str
    phase: Any = None
    phase_step: int
    step_number: str
    variant_cui: int | None = None
    variant: str
    dose_min: str | None = Field(default=None, validation_alias="doseminnum")
    dose_max: str | None = Field(default=None, validation_alias="dosemaxnum")
    dose_unit: str | None = None
    frequency: Any = None
    route: Any = None
    duration_min: str | None = Field(default=None, validation_alias="durationminnum")
    duration_max: str | None = Field(default=None, validation_alias="durationmaxnum")
    duration_unit: str | None = None
    timing_sequence: str | None = None
    cyclesigs: str | None = None
    all_days: str | None = Field(default=None, validation_alias="alldays")

    @classmethod
    def from_row(cls, sig: Sigs) -> _SigPayload:
        return cls.model_validate(sig.to_dict(include_nulls=True))


class _ComponentPayload(BaseModel):
    """Component-level grouping of sig rows."""

    component_cui: int
    component: str
    sigs: list[_SigPayload] = Field(default_factory=list)


class _IndicationPayload(BaseModel):
    """JSON representation of a HemOnc indication row."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    component_cui: int | None
    component: str
    condition_cui: int | None
    condition: str
    regulator: Any
    withdrawn: str
    stage: str | None
    status: str | None
    stage_or_status: str | None
    age: str | None
    sex: Any = None
    ineligibility: str | None
    prior_therapy: str | None
    with_field: str | None
    biomarker: str | None
    biomarker_finding: str | None
    study: str | None
    substudy: str | None
    string: str | None

    @classmethod
    def from_row(cls, indication: Indications) -> _IndicationPayload:
        return cls.model_validate(indication.to_dict(include_nulls=True))


class _RegimenContextPayload(BaseModel):
    """Complete JSON-safe regimen context returned by the provider."""

    regimen: _RegimenPayload
    components: list[_ComponentPayload]
    indications: list[_IndicationPayload]


__all__ = ["HemoncRegimenLookup"]
