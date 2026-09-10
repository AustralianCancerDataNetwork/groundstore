# groundstore

Shared mapping-task contracts and persistence for Groundworkers-adjacent
mapping workflows.

## Current status

The package provides source-independent Pydantic contracts, SQLAlchemy models,
and a resumable `MappingStore` for runs, inputs, candidates, separate evidence
records, versioned decisions, decision history, provenance, and lifecycle
state. It can use an explicit SQLAlchemy URL for tests or resolve a named
database resource through `oa-configurator` for consuming packages.

`MappingReadContext` exposes common read-only status, coverage, evidence-packet,
and review-handoff operations. Packets are JSON-safe and carry the full run,
input, candidate, evidence, and decision-history lineage, so Groundworkers or a
standalone reviewer can consume the same handoff without importing SQLAlchemy
models.

Representative contract fixtures under `tests/fixtures/` cover one-to-one,
one-to-many, ambiguous, redirected/incomplete, unmappable, and retryable
failure outcomes.

## Development

```bash
uv sync --all-extras --dev
uv run ty check src/
uv run ruff check .
uv run pytest -q
```

The package version is derived from a `vX.Y.Z` or `X.Y.Z` Git tag, with a
`0.1.0` fallback when Git metadata is unavailable.
