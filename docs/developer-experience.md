# JDO-12 — FastAPI and CI-friendly CLI

## Purpose and boundary

FastAPI and the `decisionops` command use the same `DecisionOpsApplicationService`. Contract validation, live-provider gating, deterministic policy evaluation, and replay orchestration therefore have one implementation.

The HTTP surface retains evaluation artifacts only in process memory. It is suitable for a local developer process and CI run, but it is not a durable evaluation-artifact store. Restarting the process clears `GET /v1/evaluations/{id}` results. PostgreSQL decision persistence remains the separate JDO-7 boundary.

## CLI

```bash
decisionops contract validate contracts/support-ticket-triage.yaml --json

decisionops run \
  --contract contracts/support-ticket-triage.yaml \
  --input examples/ticket.json \
  --json

decisionops eval \
  --contract contracts/support-ticket-triage.yaml \
  --dataset tests/fixtures/datasets/support-ticket-triage-v1.yaml \
  --json > current-replay.json

decisionops compare \
  --baseline baseline-replay.json \
  --current current-replay.json \
  --thresholds regression-thresholds.json \
  --json
```

`run --input` accepts one JSON object used as decision state. `eval --dataset` accepts the versioned JSON or YAML labelled-dataset format. `compare` accepts JSON artifacts emitted by `eval --json`; it deliberately compares explicit artifact files rather than guessing a baseline.

Human-readable output is the default. `--json` emits one machine-readable JSON object on standard output. `--quiet` emits neither normal output nor expected error diagnostics, which makes the exit status the only CI signal.

| Exit code | Meaning |
| --- | --- |
| `0` | Command succeeded or a regression comparison passed. |
| `1` | A comparable regression result failed an enabled quality gate. |
| `2` | Usage, file, contract, dataset, or threshold configuration error. |
| `3` | Provider execution was disabled or failed, or evaluation was partial. |
| `4` | Runs were not comparable, for example due to partial status or fingerprint mismatch. |

Live provider execution is disabled unless `LIVE_PROVIDER_CALLS_ENABLED=true` is explicitly set. A contract is always loaded and validated before the service can call a provider.

## FastAPI

Run the app locally with:

```bash
uv run uvicorn decisionops.api.app:app --reload
```

| Endpoint | Behavior |
| --- | --- |
| `POST /v1/decisions` | Validates an embedded contract and executes one decision. |
| `POST /v1/evaluations` | Validates embedded contract/dataset data and executes one replay. |
| `GET /v1/evaluations/{id}` | Reads a locally retained replay artifact. |
| `GET /v1/evaluations/{id}/report` | Reads only that artifact's metrics report. |
| `GET /health` | Process health; makes no provider or database call. |
| `GET /ready` | Checks delivery-surface local dependencies; makes no provider call. |

OpenAPI is served at `/openapi.json` and the interactive documentation at `/docs`. Every response has an `X-Correlation-ID`; a valid UUID supplied by the caller is preserved, otherwise the app generates one. Decision and evaluation responses also expose `X-Decision-Run-ID` or `X-Evaluation-ID`.

`API_MAX_REQUEST_BYTES` defaults to `65536` and accepts `1024` through `1048576`. Requests beyond that limit receive a safe `413` response before contract, dataset, or provider handling. API validation and execution errors use a centralized JSON envelope and do not echo raw state, messages, secrets, or provider debug data.
