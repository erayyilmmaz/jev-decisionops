# ADR-001 — Jev DecisionOps boundary

**Status:** Accepted
**Date:** 2026-09-20

## Context

The product needs reliable typed AI decisions, not a general agent or workflow
runtime. Combining those responsibilities would add queues, durable state,
workflow scheduling, and action execution outside V0's purpose.

## Decision

Jev DecisionOps asks narrow `Noul`, `Choice`, and `Score` questions through a
provider, combines them in deterministic application code, and evaluates the
results. It records dispositions only; it does not execute irreversible
real-world actions.

## Consequences

The V0 repository excludes workflow builders, agent execution, RAG, durable
jobs, message brokers, Redis, and a human-review UI. Callers own any action
that follows `ACT`, `REVIEW`, or `FALLBACK`.
