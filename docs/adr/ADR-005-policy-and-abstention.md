# ADR-005 — Policy and abstention semantics

**Status:** Accepted
**Date:** 2026-09-20

## Context

Probabilistic model output must not be treated as an autonomous instruction.
The product needs explainable, repeatable choices about when to act or abstain.

## Decision

Evaluate validated policy rules in declared order; the first matching rule wins.
Every contract requires a default outcome. Valid provider answers yield one of
`ACT`, `REVIEW`, or `FALLBACK`, plus a matched-rule explanation. Provider or
result-validation failure never becomes a policy outcome.

## Consequences

The same contract and typed answers always produce the same disposition. A
caller may fail safely after a provider error, but reports must identify that
as an execution failure, not claim a policy match.
