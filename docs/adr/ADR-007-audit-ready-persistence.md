# ADR-007 — Audit-ready persistence

**Status:** Accepted
**Date:** 2026-09-20

## Context

Replay and regression require enough immutable history to identify what was
run, while raw state and vendor payloads may contain sensitive data.

## Decision

Persist a run ID, contract version/fingerprint, dataset/case reference,
provider/model and dependency metadata, request ID, timestamps, latency, typed
answers, policy explanation, and sanitized failure category. Contracts are
immutable once referenced. PostgreSQL/Alembic implementation follows in JDO-7.

## Consequences

Runs can be audited and compared without storing API keys or raw input/output
by default. Required audit-write failure prevents acknowledging a new successful
run; retention and state opt-in need an explicit later policy.
