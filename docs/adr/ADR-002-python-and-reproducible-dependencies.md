# ADR-002 — Python and reproducible dependencies

**Status:** Accepted
**Date:** 2026-09-20

## Context

V0 needs typed domain models, an async provider client, FastAPI, command-line
automation, and repeatable CI.

## Decision

Use CPython `3.14.4` and `uv`. JDO-3 will create a `pyproject.toml` and commit
`uv.lock`. Pin `typesafe-sdk==0.7.0`; pin the optional JDO-10 shadow dependency
as `system-one-adapter==0.2.0`. JDO-10 selects exactly one provider extra,
OpenAI-compatible or Anthropic. Normal CI is secretless; live-provider tests
are separately triggered opt-in checks.

## Consequences

Dependency upgrades are deliberate changes with lock-file diffs, tests, and
recorded SDK/provider metadata. A `jev-latest` model alias may drift, so it is
recorded as requested configuration rather than treated as a stable model ID.
