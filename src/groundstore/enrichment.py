"""Optional, source- and target-specific candidate enrichment hooks."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol


class MappingItemEnricher(Protocol):
    """Provider that returns enriched copies of persisted mapping items."""

    def enrich_items(
        self, items: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return item copies with provider-owned review context attached."""


class MappingCandidateEnricher(MappingItemEnricher, Protocol):
    """Optional provider that adds namespaced evidence to review candidates.

    Enrichers are never applied implicitly. Their returned candidate copies
    should place provider-owned data under ``target_enrichments[namespace]`` so
    unrelated providers can compose without sharing a schema or key space.
    """

    namespace: str


def enrich_candidate_targets(
    items: Iterable[Mapping[str, Any]],
    *,
    namespace: str,
    enrichments: Mapping[str, Any],
    include: Callable[[Mapping[str, Any]], bool] | None = None,
    target_field: str = "target_concept_id",
) -> list[dict[str, Any]]:
    """Attach a namespaced payload to matching candidate target IDs.

    Providers use this helper to share the copy-on-write and namespacing
    mechanics while keeping target selection and payload construction in the
    provider that owns those semantics.

    ``target_field`` defaults to ``target_concept_id``. Providers whose target
    identity is a source code may explicitly use ``target_code``.
    """
    copied_items = [dict(item) for item in items]
    for item in copied_items:
        enriched_candidates = []
        for candidate in item.get("candidates", []):
            enriched = dict(candidate)
            target_id = str(candidate.get(target_field))
            eligible = include is None or include(candidate)
            if eligible and target_id in enrichments:
                target_enrichments = dict(enriched.get("target_enrichments", {}))
                target_enrichments[namespace] = enrichments[target_id]
                enriched["target_enrichments"] = target_enrichments
            enriched_candidates.append(enriched)
        item["candidates"] = enriched_candidates
    return copied_items


def apply_candidate_enrichers(
    items: Iterable[Mapping[str, Any]],
    enrichers: Iterable[MappingCandidateEnricher],
) -> list[dict[str, Any]]:
    """Apply optional candidate enrichers in the supplied deterministic order."""
    enriched = [dict(item) for item in items]
    for enricher in enrichers:
        enriched = enricher.enrich_items(enriched)
    return enriched


def close_enrichers(enrichers: Iterable[object]) -> None:
    """Close provider resources that expose the optional ``close`` method."""
    seen: set[int] = set()
    for enricher in enrichers:
        identity = id(enricher)
        if identity in seen:
            continue
        seen.add(identity)
        close = getattr(enricher, "close", None)
        if callable(close):
            close()
