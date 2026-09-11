# groundstore

Shared mapping-task contracts and persistence for Groundworkers-adjacent
mapping workflows.

## Current status

The package provides source-independent Pydantic contracts, SQLAlchemy models, and a resumable `MappingStore` for runs, inputs, candidates, separate evidence records, versioned decisions, decision history, provenance, and lifecycle state. It can use an explicit SQLAlchemy URL for tests or resolve a named database resource through `oa-configurator` for consuming packages.

`MappingReadContext` exposes common read-only status, coverage, paginated review summaries, evidence-packet, and review-handoff operations. Packets are JSON-safe and carry the full run, input, candidate, evidence, and decision-history lineage, so Groundworkers or a standalone reviewer can consume the same handoff without importing SQLAlchemy models. Review pages are query-level paginated and accept source-defined decision-status filters; `pending` is synthesized for inputs without a decision.

## Development

```bash
uv sync --all-extras --dev
uv run ty check src/
uv run ruff check .
uv run pytest -q
```
