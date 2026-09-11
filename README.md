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

## Review interface

Install the optional Groundskeeping interface with the released `0.9.x` shell:

```bash
uv sync --extra tui
uv run groundstore-review --source-namespace pbs --cdm-database cdm_db
```

The command resolves `mapping_db` through the active `oa-configurator` configuration. For an
explicit mapping database URL, use `--url`; `--cdm-database` names the resolved OMOP CDM
resource (default `cdm_db`). Library users pass the resolved CDM database to
`create_mapping_review_app` from `groundstore.tui`; the review UI keeps the mapping store and
CDM engines separate.
