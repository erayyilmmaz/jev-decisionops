# ADR-008 — Security and data boundaries

**Date:** 2026-09-20
**Status:** Accepted
**Date:** 2026-09-20
**Date:** 2026-09-20

## Context

Provider input, SDK debug output, LLM attempt history, and telemetry can expose
customer data or secrets if captured indiscriminately.

## Decision

Use environment-based secret injection only. Disable provider debug logging by
default. Do not log or persist keys, authorization headers, raw state, raw
provider bodies, or shadow attempt history by default. Do not use state,
question text, or high-cardinality IDs as metric labels. Bound request size,
timeout, concurrency, and outbound provider configuration in later stories.

## Consequences

Operational traces are useful without becoming a data store. Any future
raw-state retention, diagnostic capture, or live test requires explicit
redaction, access, retention, and opt-in decisions.
