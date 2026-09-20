# Jev DecisionOps

Decision quality and reliability layer for typed probabilistic workflows with Jev.

## V0 technical baseline

The accepted V0 scope, decision semantics, failure boundaries, and architecture
decisions are documented in [docs/technical-baseline.md](docs/technical-baseline.md).
The individual Architecture Decision Records are indexed in
[docs/adr/README.md](docs/adr/README.md).

## Development

```bash
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

See [docs/development.md](docs/development.md) for local PostgreSQL and the
explicit boundary around live provider calls.
