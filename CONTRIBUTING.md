# Contributing

## Development setup

```bash
uv sync --all-extras --dev
uv run ty check src/
uv run ruff check .
uv run pytest -q
```

Pull requests should carry exactly one of the repository labels:
`breaking`, `feature`, `fix`, `dependencies`, or `chore`. Public API changes
are breaking changes; compatible functionality is a feature; CI, refactoring,
tests, and documentation are chore changes.

Package versions are derived from Git tags. Releases are published from a
maintainer-approved `vX.Y.Z` tag through the repository's PyPI environment.
