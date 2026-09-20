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

## Decision Contracts

V1 contract syntax, validation guarantees, and a runnable example are described
in [docs/decision-contract.md](docs/decision-contract.md). Validation is local
and does not require `TYPESAFE_API_KEY`.

## Policy Engine

The deterministic `ACT` / `REVIEW` / `FALLBACK` policy semantics and trace
format are documented in [docs/policy-engine.md](docs/policy-engine.md).

## Decision audit persistence

The PostgreSQL audit record model, transactional write boundary, and the safe
data-retention boundary are documented in
[docs/persistence.md](docs/persistence.md).

## Labelled evaluation datasets

The versioned synthetic dataset format, contract binding, fingerprinting, and
fixture suite are documented in
[docs/evaluation-datasets.md](docs/evaluation-datasets.md).

## Metrics and calibration

The deterministic scoring formulas, calibration semantics, and reproducible JSON
report boundary are documented in [docs/evaluation-metrics.md](docs/evaluation-metrics.md).

## LLM shadow provider

The isolated OpenAI-compatible shadow path and its raw-debug data boundary are
documented in [docs/shadow-provider.md](docs/shadow-provider.md).

## Replay and regression gates

Bounded dataset replay, explicit baseline compatibility, and versioned quality
gates are documented in [docs/replay-regression.md](docs/replay-regression.md).

## FastAPI and CLI

The local FastAPI surface, CI-friendly CLI, JSON artifacts, stable exit codes,
and delivery-surface security limits are documented in
[docs/developer-experience.md](docs/developer-experience.md).

## Telemetry and operational safeguards

Low-cardinality Prometheus metrics, OpenTelemetry span boundaries, request and
dataset limits, provider controls, and the container security boundary are
documented in [docs/security-operations.md](docs/security-operations.md).

## V0 release and synthetic demo

The architecture diagram, secretless 500-case synthetic demo, CI boundaries,
methodology, limitations, and release checklist are in
[docs/v0-release.md](docs/v0-release.md).
