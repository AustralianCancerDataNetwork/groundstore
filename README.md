# Groundstore

Groundstore is a small persistence layer for mapping work. It gives a mapping workflow one durable place to record what was processed, what the algorithm proposed, what evidence supported those proposals, and what a person or downstream system eventually decided.

It is deliberately source-independent. A source can be a terminology extract, a catalogue, a clinical resource, or any other collection of things that need to be mapped. A target can be a vocabulary, a local reference set, a clinical data model, or another controlled identifier system. Groundstore stores the workflow and its provenance; the code that understands a particular source or target remains outside the core contract.

## The mental model

A mapping run is one reproducible attempt to process a source snapshot with a named algorithm and policy. Each run contains mapping inputs. An input is one source item, identified by a stable source key and fingerprint. An input can have zero or more proposed candidates, zero or more evidence records, and an append-only history of decisions.

The resulting shape is:

```text
Mapping run
└── Mapping input
    ├── Candidate 1, Candidate 2, ...
    ├── Evidence for a candidate or decision
    └── Decision v1, Decision v2, ...
```

This separation matters. An algorithm may propose several plausible targets without claiming that any of them is correct. Evidence explains why a proposal was made. A decision records an accepted, rejected, deferred, or otherwise reviewed outcome without overwriting earlier decisions.

## What Groundstore guarantees

Groundstore provides durable identity and resumability for runs and inputs, deterministic retrieval order for review pages, idempotent upsert operations for repeated mapping work, separate evidence records, versioned decisions, decision history, provenance, lifecycle and error state, and JSON-safe read models for user interfaces or other consumers.

The store does not decide whether a candidate is correct. It does not impose a universal confidence threshold, source-specific status vocabulary, or review policy. Those are properties of the mapping application and its algorithm version. Groundstore records them so that a result can be understood and reproduced later.

## A typical workflow

1. Resolve the mapping database through the active `oa-configurator` configuration, or provide an explicit SQLAlchemy database URL for a test or isolated process.
2. Create a `MappingRunSpec` containing the source namespace, source fingerprint, target system and release, algorithm version, policy version, and source snapshot metadata.
3. Obtain the stable run with `MappingStore.get_or_create_run(...)`.
4. Upsert each `MappingInput` and its `MappingCandidate` records. Re-running the same source and algorithm reuses the same identities instead of duplicating work.
5. Attach evidence to candidates or decisions with `MappingStore.add_evidence(...)`.
6. Update lifecycle state as work moves through the queue. Failures remain attached to the run or input through `last_error` and retry counts.
7. Record reviewed outcomes with `MappingStore.record_decision(...)`. Decisions are versioned and previous versions remain available.
8. Use `MappingReadContext` or the store's query methods to report progress, inspect coverage, build review pages, and hand a complete review packet to another interface.

Durable manual corrections live outside a run. Create a `MappingOverrideSpec` with the
adapter's source identity and confirmed fingerprint, then call
`MappingStore.upsert_override(...)`. Replacements retire the previous row atomically;
`get_override(...)` only returns the active row. `import_overrides(...)` validates the
whole batch before writing, so a malformed or duplicate row cannot leave a partial seed
set. The mapper consumer is responsible for comparing the current fingerprint and for
recording an applied override as a `manual_override` decision origin.

## Installation

The base package contains the contracts, models, store, and read APIs:

```bash
uv add groundstore
```

The optional extras add integrations rather than changing the core model:

```bash
uv add 'groundstore[tui]'
uv add 'groundstore[omop]'
uv add 'groundstore[cancer]'
uv add 'groundstore[hemonc]'
```

The `tui` extra installs the standalone review interface and database driver support. The `omop`, `cancer`, and `hemonc` extras install the corresponding optional lookup providers. Providers are opt-in: installing one does not automatically enrich every candidate or change the mapping result.

The `cancer` extra requires the compatible `omop-alchemy` release that provides the public concept-query helpers used by that provider. Until that release is available, install the other extras you need and leave `cancer` out of the environment.

## Database configuration

Groundstore follows the same resource-resolution pattern as the surrounding stack. `MappingStore.from_database(...)` accepts an `oa-configurator` `ResolvedDatabase`, and `MappingStore.from_url(...)` is useful for tests and small standalone processes.

```python
from groundstore import MappingStore

store = MappingStore.from_url("sqlite:///mapping.db")
try:
    # create tables in the application/bootstrap layer, then use the store
    ...
finally:
    store.close()
```

`MappingStore` is also a context manager, so short-lived processes can use `with MappingStore.from_url(...) as store:`. The store owns the SQLAlchemy engine it creates; callers that pass providers or database resources into a longer-lived application remain responsible for those resources.

## Review and read APIs

`MappingReadContext` is the read-oriented facade. It exposes run summaries, lifecycle and decision coverage, progress snapshots, paginated input queries, review pages, evidence packets, and review handoffs. These methods return plain structures suitable for a CLI, a terminal UI, an API, or a notebook without requiring those consumers to know the ORM models.

The optional review command provides a standalone terminal view:

```bash
uv run groundstore-review --source-namespace SOURCE --url sqlite:///mapping.db
```

When a resolved OMOP database is needed for candidate context, pass its configured resource name with `--cdm-database`. The mapping database and the resolved reference database remain separate resources.

## Enrichment providers

Groundstore defines a small `MappingItemEnricher` protocol for optional item-level context and a `MappingCandidateEnricher` protocol for optional candidate-target context. A provider can add useful context without changing the core mapping tables or forcing every consumer to pay the cost of every lookup.

Enrichment is explicit. A caller composes the providers it wants for a particular review or mapping operation, and can use the provider directly when it needs a specialised query. This keeps common functionality reusable while avoiding a large, implicit enrichment pipeline.

## Retention and cleanup

Groundstore treats mapping results as historical workflow records. A refresh or rerun does not delete source items that have disappeared from a later source snapshot. If a consumer needs to remove old work, it can find runs using exact identity filters such as source namespace, target system, algorithm version, or policy version and then pass the selected run IDs to `MappingStore.delete_runs(...)`.

Deletion cascades through the selected run's inputs, candidates, evidence, decisions, and decision history. The library intentionally leaves confirmation and user-interface policy to the application using it.

## Development

```bash
uv sync --extra dev --extra tui --extra omop --extra hemonc
uv run ty check src/
uv run ruff check .
uv run pytest -q
```

The optional cancer provider uses the public query interfaces from a compatible `omop-alchemy` release. The provider's extra is intentionally constrained to that helper-containing release; once it is published, add `--extra cancer` to the sync command and refresh the lock file.
