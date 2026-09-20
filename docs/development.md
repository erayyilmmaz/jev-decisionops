# JDO-3 development foundation

## Purpose

This repository is a Python `src/` layout project managed by `uv`. It exposes
provider-independent domain models and empty delivery/persistence boundaries so
that future stories do not couple core policy/evaluation logic to FastAPI, CLI,
or a TypeSafe SDK client.

## Local setup

```bash
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

Expected result: each command exits with code `0`. These checks do not call
TypeSafe, another LLM provider, Docker, or PostgreSQL.

## Local PostgreSQL

Copy `.env.example` to an untracked `.env` only if a later database story needs
it. Start the local development database with:

```bash
docker compose up -d postgres
docker compose ps
```

Expected result: `postgres` becomes healthy on local port `5432` unless
`POSTGRES_PORT` is overridden. `decisionops_dev_only` is a public local-dev
placeholder, not a production credential; replace it only in an untracked
environment file.

No migration is defined in JDO-3. JDO-7 will add tables and an initial Alembic
revision. Bringing up Docker successfully is separate evidence from migrations
running successfully.

## Live-provider boundary

`TYPESAFE_API_KEY` is empty by default and `LIVE_PROVIDER_CALLS_ENABLED=false`.
Normal tests and future pull-request CI must remain secretless. A live provider
smoke test will be added later as an explicitly dispatched workflow; it must
never be needed for local unit tests.

## Current package map

```text
src/decisionops/
  api/          FastAPI app factory only
  cli/          Console entry-point foundation only
  contracts/    JDO-4 contract loader and validation
  evaluation/   JDO-8 through JDO-11 evaluation services
  persistence/  SQLAlchemy/Alembic foundation; entities in JDO-7
  policy/       JDO-6 deterministic policy engine
  providers/    Provider protocol; Jev adapter in JDO-5
  telemetry/    JDO-13 instrumentation
```
