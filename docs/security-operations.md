# JDO-13 — Telemetry, security boundaries, and operational safeguards

## Telemetry contract

The delivery service emits OpenTelemetry spans for `decision.execute`, `provider.call`, `policy.evaluate`, `evaluation.case`, and `evaluation.aggregate`. Spans carry no request state, message, contract/dataset fingerprint, run ID, API key, provider body, or policy trace attributes.

The service obtains its tracer from the active OpenTelemetry provider and has no mandatory remote exporter. This means an unavailable telemetry backend cannot stop a decision or evaluation. A deployment may configure the global provider or supply a provider to the service, but exporter configuration and collector operations are outside this V0 repository boundary.

`GET /metrics` exposes a dedicated Prometheus registry. The metric inventory is intentionally small:

| Metric | Labels | Purpose |
| --- | --- | --- |
| `decisionops_provider_requests_total` | normalized `provider` | Provider request volume. |
| `decisionops_provider_errors_total` | normalized `provider`, fixed `failure_kind` | Provider failure rate. |
| `decisionops_provider_latency_seconds` | normalized `provider` | Provider latency distribution. |
| `decisionops_policy_outcomes_total` | fixed `outcome` | ACT/REVIEW/FALLBACK volume. |
| `decisionops_evaluation_duration_seconds` | none | Full replay duration. |
| `decisionops_regression_comparisons_total` | fixed `outcome` | PASS/FAIL/INCOMPARABLE counts. |

Only known provider names are retained as labels; any other value becomes `other`. There are no labels for user state, messages, question text, case ID, run ID, contract/dataset fingerprint, request ID, exception text, or credential material. Provider response logging is disabled by design. Structured log events contain only fixed outcome/provider/failure fields and defensively redact field names such as `api_key`, `secret`, `token`, `state`, `message`, `body`, and `response`.

## Runtime safeguards

| Control | Default | Boundary |
| --- | --- | --- |
| `API_MAX_REQUEST_BYTES` | `65536` | API rejects larger request bodies with `413` before contract, dataset, or provider handling. |
| `MAX_DATASET_CASES` | `1000` | Evaluation rejects larger declared datasets before provider execution. |
| `EVALUATION_MAX_CONCURRENCY` | `4` | Replay is bounded to `1` through `16` concurrent provider calls. |
| `TYPESAFE_TIMEOUT_SECONDS` | `10` | Official Jev provider timeout is bounded to `(0, 60]` seconds. |
| `TYPESAFE_MAX_RETRIES` | `2` | Official Jev retry count is bounded to `0` through `3`. |
| `OUTBOUND_PROVIDER_ALLOWLIST` | `typesafe_jev` | Default service refuses an outbound provider configuration outside the allow-list. |
| `LIVE_PROVIDER_CALLS_ENABLED` | `false` | No live provider call occurs until explicitly enabled. |

The existing persistence repository uses SQLAlchemy ORM entities and bound query expressions; it does not build SQL from caller input. That data-boundary assurance is separate from the in-memory FastAPI evaluation artifact store.

## Container and operator checks

The Docker image creates `decisionops` with UID `10001` and finishes with `USER decisionops`; the runtime command does not run as root. It does not prove that a container image was built or deployed in this story.

For a local API check, start the app and inspect only non-sensitive signals:

```bash
uv run uvicorn decisionops.api.app:app --host 127.0.0.1 --port 8000
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
curl --fail http://127.0.0.1:8000/metrics
```

Expected results are `health: ok`, `ready: ready`, and Prometheus text metrics. These endpoints make no live provider call. A full Docker build, external OpenTelemetry collector integration, database connectivity, and live Jev verification remain separate environment-level checks.
