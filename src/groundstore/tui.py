"""Optional Groundskeeping review interface for persisted mapping work."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from groundskeeping.app import OperatorApp, OperatorAppSpec
from groundskeeping.contracts import (
    EmptyView,
    KeyValueView,
    NavigationItem,
    PageContext,
    PageRegistration,
    PageRoute,
    Pagination,
    SectionItem,
    SectionNavigation,
    SemanticStatus,
    SurfaceView,
    TableRow,
    TableView,
    TextView,
    ViewAction,
    pagination_actions,
)
from oa_configurator import ResolvedCDMDatabase, Resolver
from textual.widget import Widget

from .cdm import CdmConceptLookup
from .context import MappingReadContext
from .context import MappingReviewPage as ReviewPageData
from .store import MappingStore

MAPPING_REVIEW_ROUTE = PageRoute(
    key="mapping-review",
    label="Mapping review",
    purpose="Inspect persisted mapping inputs and evidence.",
)

_FILTERS: tuple[tuple[str, str, SemanticStatus], ...] = (
    ("review.all", "All inputs", SemanticStatus.INFO),
    ("review.pending", "Pending", SemanticStatus.WARNING),
    ("review.needs_review", "Needs review", SemanticStatus.WARNING),
    ("review.ambiguous", "Ambiguous", SemanticStatus.WARNING),
    ("review.mapped", "Mapped", SemanticStatus.OK),
    ("review.unmappable", "Unmappable", SemanticStatus.INFO),
)


class MappingReviewOperatorPage(Widget):
    """Groundskeeping page backed by an existing Groundstore read context."""

    route = MAPPING_REVIEW_ROUTE

    def __init__(
        self,
        read_context: MappingReadContext,
        *,
        source_namespace: str,
        concept_lookup: CdmConceptLookup,
        run_id: str | None = None,
        target_system: str | None = None,
        page_size: int = 20,
    ) -> None:
        super().__init__()
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._read_context = read_context
        self._concept_lookup = concept_lookup
        self._source_namespace = source_namespace
        self._run_id = run_id
        self._target_system = target_system
        self._page_size = page_size
        self._page = 1
        self._page_count = 1
        self._decision_status: str | None = None
        self._items: dict[str, dict[str, Any]] = {}

    def activate(self, context: PageContext) -> None:
        return None

    def deactivate(self, context: PageContext) -> None:
        return None

    def build_navigation(self, context: PageContext) -> SectionNavigation:
        return SectionNavigation(
            title="Review filter",
            items=tuple(
                SectionItem(
                    key,
                    label,
                    status=status,
                    description=(
                        "currently selected"
                        if self._filter_key(key) == self._decision_status
                        else None
                    ),
                )
                for key, label, status in _FILTERS
            ),
        )

    def landing_view(self, context: PageContext) -> SurfaceView:
        return self._load_view()

    def navigation_selected(self, item: NavigationItem, context: PageContext) -> None:
        if not isinstance(item, SectionItem) or not item.key.startswith("review."):
            return
        self._decision_status = self._filter_key(item.key)
        self._page = 1
        context.surface.show_view(self.route.key, self._load_view())

    def action_selected(self, action_key: str, context: PageContext) -> None:
        if action_key == "pagination.previous":
            self._page = max(1, self._page - 1)
        elif action_key == "pagination.next":
            self._page = min(self._page_count, self._page + 1)
        elif action_key != "review.refresh":
            return
        context.surface.refresh_view(self.route.key, self._load_view())

    def row_highlighted(self, row_key: str, context: PageContext) -> None:
        item = self._items.get(row_key)
        if item is not None:
            context.surface.show_detail(self.route.key, _summary_detail(item))

    def row_selected(self, row_key: str, context: PageContext) -> None:
        try:
            handoff = self._read_context.review_handoff(row_key)
        except KeyError as exc:
            context.notify(str(exc), severity="error")
            return
        payload = handoff.model_dump(mode="json")
        item = self._items.get(row_key)
        if item is not None:
            payload["cdm_concepts"] = {
                str(candidate["target_concept_id"]): candidate["cdm_concept"]
                for candidate in item.get("candidates", [])
                if candidate.get("cdm_concept") is not None
            }
        context.surface.show_detail(
            self.route.key,
            TextView(
                title=f"Review packet · {handoff.input_id}",
                body=json.dumps(payload, indent=2, sort_keys=True),
            ),
        )

    def _load_view(self) -> SurfaceView:
        page = self._read_context.review_page(
            self._source_namespace,
            run_id=self._run_id,
            target_system=self._target_system,
            page=self._page,
            page_size=self._page_size,
            decision_status=self._decision_status,
        )
        if page.run is None:
            self._items = {}
            return EmptyView(
                title="Mapping review",
                message=(
                    f"No completed {self._source_namespace} mapping run is available."
                ),
            )

        self._page_count = max(1, page.page_count)
        self._page = min(page.page, self._page_count)
        self._items = {
            str(item["input_id"]): item
            for item in self._concept_lookup.enrich_items(page.items)
        }
        pagination = Pagination(
            page=self._page,
            page_size=page.page_size,
            total_items=page.total_items,
        )
        actions = (
            *pagination_actions(pagination),
            ViewAction("review.refresh", "Refresh"),
        )
        return TableView(
            title=f"{self._source_namespace} mapping review",
            columns=("Kind", "Input", "Decision", "Proposed maps", "Match"),
            rows=tuple(
                TableRow(
                    str(item["input_id"]),
                    (
                        str(item["source_kind"]),
                        _source_label(item),
                        str(item["decision_status"]),
                        _candidate_summary(item),
                        _match_summary(item),
                    ),
                    detail=item,
                )
                for item in self._items.values()
            ),
            status=_status_for(self._decision_status),
            message=_page_message(page),
            actions=actions,
            pagination=pagination,
        )

    @staticmethod
    def _filter_key(key: str) -> str | None:
        return None if key == "review.all" else key.removeprefix("review.")


def create_mapping_review_app(
    store: MappingStore,
    *,
    source_namespace: str,
    cdm_database: ResolvedCDMDatabase | None = None,
    concept_lookup: CdmConceptLookup | None = None,
    run_id: str | None = None,
    target_system: str | None = None,
    page_size: int = 20,
    title: str = "Groundstore mapping review",
) -> OperatorApp:
    """Create a review app backed by a resolved OMOP CDM vocabulary."""
    if (cdm_database is None) == (concept_lookup is None):
        raise ValueError("provide exactly one of cdm_database or concept_lookup")
    if concept_lookup is not None:
        resolved_lookup = concept_lookup
    else:
        assert cdm_database is not None
        resolved_lookup = CdmConceptLookup.from_database(cdm_database)
    page = MappingReviewOperatorPage(
        MappingReadContext(store),
        source_namespace=source_namespace,
        concept_lookup=resolved_lookup,
        run_id=run_id,
        target_system=target_system,
        page_size=page_size,
    )
    return OperatorApp(
        OperatorAppSpec(
            app_id=f"groundstore-review-{source_namespace}",
            title=title,
            subtitle=f"source: {source_namespace}",
            pages=(PageRegistration(MAPPING_REVIEW_ROUTE, lambda _context: page),),
            default_page=MAPPING_REVIEW_ROUTE.key,
        )
    )


def run_mapping_review(
    store: MappingStore,
    *,
    source_namespace: str,
    cdm_database: ResolvedCDMDatabase | None = None,
    concept_lookup: CdmConceptLookup | None = None,
    run_id: str | None = None,
    target_system: str | None = None,
    page_size: int = 20,
) -> None:
    """Run the standalone review app for an existing store and resolved CDM."""
    owns_lookup = concept_lookup is None
    lookup = concept_lookup
    if lookup is None:
        resolved_database = cdm_database
        if resolved_database is None:
            raise ValueError("cdm_database is required when concept_lookup is omitted")
        lookup = CdmConceptLookup.from_database(resolved_database)
    try:
        create_mapping_review_app(
            store,
            source_namespace=source_namespace,
            concept_lookup=lookup,
            run_id=run_id,
            target_system=target_system,
            page_size=page_size,
        ).run()
    finally:
        if owns_lookup:
            lookup.close()


def main(argv: Sequence[str] | None = None) -> None:
    """Run review using an explicit URL or the active oa-configurator resource."""
    parser = argparse.ArgumentParser(description="Review Groundstore mapping inputs.")
    parser.add_argument("--source-namespace", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--target-system")
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--database", default="mapping_db")
    parser.add_argument("--cdm-database", default="cdm_db")
    parser.add_argument("--url", help="Explicit SQLAlchemy database URL")
    args = parser.parse_args(argv)

    resolver = Resolver.from_active_config()
    cdm_database = resolver.resolve_database(args.cdm_database)
    if not isinstance(cdm_database, ResolvedCDMDatabase):
        raise TypeError(
            f"{args.cdm_database!r} must resolve to a CDM database, "
            f"got {type(cdm_database).__name__}"
        )
    store = (
        MappingStore.from_url(args.url)
        if args.url
        else MappingStore.from_resolver(
            resolver, database_name=args.database
        )
    )
    try:
        run_mapping_review(
            store,
            source_namespace=args.source_namespace,
            cdm_database=cdm_database,
            run_id=args.run_id,
            target_system=args.target_system,
            page_size=args.page_size,
        )
    finally:
        store.engine.dispose()


def _status_for(decision_status: str | None) -> SemanticStatus:
    if decision_status in {"pending", "needs_review", "ambiguous"}:
        return SemanticStatus.WARNING
    if decision_status == "mapped":
        return SemanticStatus.OK
    return SemanticStatus.INFO


def _page_message(page: ReviewPageData) -> str:
    run_id = str(page.run["id"])[:8] if page.run is not None else "-"
    filter_label = page.decision_status or "all statuses"
    return f"Run {run_id} · {filter_label} · select a row for the review packet"


def _candidate_summary(item: dict[str, Any]) -> str:
    candidates = item.get("candidates", [])
    if not candidates:
        return "-"
    return "; ".join(_candidate_label(candidate) for candidate in candidates)


def _source_label(item: dict[str, Any]) -> str:
    projection = item.get("normalized_projection", {})
    if not isinstance(projection, dict):
        return _shorten(str(projection))

    preferred_keys = (
        ("drug_name", "li_drug_name", "brand_name")
        if item.get("source_kind") == "drug"
        else ("text", "name", "description")
    )
    for key in preferred_keys:
        value = projection.get(key)
        if value is not None and str(value).strip():
            return _shorten(str(value))
    return _shorten(str(item.get("source_key", "-")))


def _match_summary(item: dict[str, Any]) -> str:
    candidates = item.get("candidates", [])
    if not candidates:
        return "-"
    methods = ", ".join(
        dict.fromkeys(str(candidate.get("method", "unknown")) for candidate in candidates)
    )
    confidences = [
        float(candidate["confidence"])
        for candidate in candidates
        if candidate.get("confidence") is not None
    ]
    if not confidences:
        return methods
    return f"{methods} · best {max(confidences):.2f}"


def _shorten(value: str, limit: int = 72) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1].rstrip()}…"


def _candidate_label(candidate: dict[str, Any]) -> str:
    identifier = (
        candidate.get("target_concept_id")
        or candidate.get("target_code")
        or candidate.get("target_vocabulary_id")
        or "?"
    )
    target_vocabulary = candidate.get(
        "target_vocabulary_id", candidate.get("target_namespace", "target")
    )
    method = candidate.get("method", "unknown")
    concept = candidate.get("cdm_concept")
    if concept is None:
        return f"{target_vocabulary}:{identifier} [{method}]"
    return (
        f"{target_vocabulary}:{identifier} · {concept['concept_name']} "
        f"({concept['concept_class_id']}) [{method}]"
    )


def _summary_detail(item: dict[str, Any]) -> KeyValueView:
    return KeyValueView(
        title=f"Input · {_source_label(item)}",
        rows=(
            ("Input ID", str(item["input_id"])),
            ("Source kind", str(item["source_kind"])),
            ("Input", _source_label(item)),
            ("Source key", str(item["source_key"])),
            ("Decision", str(item["decision_status"])),
            ("Lifecycle", str(item["lifecycle_status"])),
            (
                "Normalized projection",
                json.dumps(item.get("normalized_projection", {}), indent=2, sort_keys=True),
            ),
            ("Candidates", _candidate_summary(item)),
            ("Match", _match_summary(item)),
        ),
    )
