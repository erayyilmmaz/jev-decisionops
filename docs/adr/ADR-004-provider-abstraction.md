# ADR-004 — Provider abstraction boundary

**Status:** Accepted
**Date:** 2026-09-20

## Context

Jev and the optional LLM shadow adapter expose related but provider-specific
client configuration, response metadata, and failures.

## Decision

The core domain depends on a `DecisionProvider` protocol and common typed
answers/results. Provider implementations translate contracts to SDK question
objects, enforce bounded timeout/retry, measure monotonic latency, and map SDK
errors to execution failures. They do not implement policy or metrics.

## Consequences

Unit tests use fakes and do not call external APIs. JDO-10 can add one shadow
provider without coupling FastAPI, CLI, policy, or evaluation code to its SDK.
Raw SDK debug/result bodies stay at the provider boundary.
