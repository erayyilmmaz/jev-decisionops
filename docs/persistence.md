# JDO-7 — Decision persistence and audit-ready execution records

## Purpose

JDO-7 persists the reproducibility evidence of a decision execution in
PostgreSQL. It records the exact validated Decision Contract fingerprint, a
sanitized provider result or provider failure, typed answers, and the
deterministic policy trace when available.

`record_execution(...)` is the only current application write boundary. It
adds the entire execution to the caller's `AsyncSession`, flushes to surface
database errors, and leaves `commit()` or `rollback()` to the caller. A
duplicate `run_id` is rejected before any new audit row is added.

## Stored record shape

| Table | Stored evidence |
| --- | --- |
| `decision_contracts` | Contract name, version, canonical JSON, and SHA-256 fingerprint; fingerprints are unique. |
| `decision_runs` | One success or failure attempt, timestamps, safe provider/model metadata, latency/token counts, and sanitized failure category/message. |
| `decision_answers` | One typed JSONB answer per `(run_id, question_id)`. |
| `policy_outcomes` | At most one final policy outcome and its deterministic rule-evaluation trace per run. |

The contract record is deduplicated by fingerprint. An execution may reference
an existing contract record, but a run ID cannot be reused. Successful runs
may have answers and one policy outcome. Failed runs have neither: a provider
failure is not converted into a policy disposition.

## Safe data boundary

The persistence API has no `state` parameter. It does **not** store raw caller
state, `TYPESAFE_API_KEY`, authorization headers, provider debug payloads,
exception stacks, or retry internals. Failure records accept the existing
sanitized `ProviderFailure` domain model only; the Jev adapter emits fixed,
safe messages and an optional request ID.

The policy trace is limited to the safe fields established in JDO-6: rule ID,
question ID, predicate name, observed value, expected value, and match result.
The canonical contract JSON can contain contract authors' instructions and
criteria, so contract authors must not place customer data or secrets in a
contract.

## Migration and local verification

The initial PostgreSQL revision is
`20260920_01_decision_audit_records`. Generate its offline SQL without opening
a database connection:

```bash
uv run alembic -c alembic.ini upgrade head --sql
```

To apply it to the local PostgreSQL service described in
[development.md](development.md), first start that service and then run:

```bash
uv run alembic -c alembic.ini upgrade head
```

Offline SQL generation verifies the revision can render for PostgreSQL. It is
not evidence that a live database accepted the migration; a local database run
is a separate integration gate. Normal unit tests and migrations do not call
the live Jev API.
