# JDO-2 — Technical baseline and evaluation semantics

**Status:** Accepted
**Jira:** JDO-2
**Last reviewed:** 2026-09-20

## Purpose

Jev DecisionOps V0 is a **decision-quality and reliability layer**. It asks
TypeSafe Jev for small, typed judgements, applies ordinary deterministic code
to those results, records the execution safely, and evaluates quality against
labelled data. It is not a Jev workflow runtime, agent framework, prompt
platform, human-review UI, or system that performs irreversible real-world
actions.

This document is the implementation baseline for JDO-3 onward. A later story
may extend it only through a new ADR; it must not silently change the meanings
of probability, confidence, a provider failure, or a quality failure.

## Authoritative sources

The sources below were reviewed on 2026-09-20. They are vendor/API references,
not evidence that a particular model is suitable for this product.

- [TypeSafe Python SDK](https://docs.typesafe.ai/sdk/python)
- [TypeSafe Python SDK usage](https://docs.typesafe.ai/sdk/python/usage)
- [TypeSafe primitives](https://docs.typesafe.ai/primitives)
- [TypeSafe workflow evaluation methodology](https://evals.typesafe.ai/)
- [Official System One Adapter](https://github.com/typesafe-ai/system-one-adapter-python)

## Runtime and dependency baseline

| Item | V0 decision | Why |
| --- | --- | --- |
| Language/runtime | CPython `3.14.4` | Available locally; the official TypeSafe SDK and adapter declare Python 3.14 support. |
| Dependency manager | `uv` | One lock file makes clean-checkout installs reproducible. |
| TypeSafe SDK | `typesafe-sdk==0.7.0` | Pinned provider surface; no floating `latest` dependency. |
| Shadow adapter | `system-one-adapter==0.2.0` | Pinned only when JDO-10 enables the optional shadow path; that story selects exactly one provider extra, OpenAI-compatible or Anthropic. |
| Default Jev model alias | `jev-latest` | Record the requested alias on every run; never treat it as an immutable resolved model ID. |
| Live calls | Explicit opt-in only | Unit/normal CI must be secretless and must not call external providers. |

JDO-3 must create `pyproject.toml` and `uv.lock` from these pins. The lock file
is the source of truth for resolved transitive versions and hashes. A failed
resolution is a JDO-3 blocker, not a reason to relax a pin silently.

## Architecture boundary

```text
Versioned Decision Contract + state
              |
              v
       Provider abstraction
       /                  \\
  Jev provider       optional LLM shadow
       |                  |
       +------ typed DecisionAnswers ------+
                                           |
                                           v
                            deterministic Policy Engine
                            ACT / REVIEW / FALLBACK
                                           |
                                           v
                          audit-ready DecisionRun records
                                           |
                                           v
                     dataset evaluation / calibration / regression
```

The provider does not own policy. The policy does not call an AI service. An
evaluation or telemetry failure does not modify a production policy decision.

## Domain vocabulary

| Term | Meaning |
| --- | --- |
| Decision Contract | Versioned, declarative YAML document containing questions and policy; it contains no executable Python. |
| Contract fingerprint | SHA-256 of the canonical contract JSON (UTF-8, sorted object keys, normalized primitive values). |
| Decision Run | One attempt to evaluate one contract against one input state using one provider configuration. |
| Policy outcome | Deterministic `ACT`, `REVIEW`, or `FALLBACK`; it exists only after a valid provider result. |
| Evaluation Case | One synthetic/labelled state and expected answers, identified by a stable case ID. |
| Dataset fingerprint | SHA-256 of canonical validated cases, including their order and schema version. |
| Evaluation Run | A reproducible run over a dataset, retaining contract, dataset, provider, and metric metadata. |
| Shadow Run | An optional independent provider execution against the same contract/state. It never overwrites Jev's result. |

## Primitive and evaluation semantics

| Primitive | Provider result used by policy | Ground truth | Quality and calibration value | Explicit non-equivalence |
| --- | --- | --- | --- | --- |
| `Noul` | `noul` in `[0,1]` | boolean | Brier/log loss/ECE use `noul` against `1` or `0` | `Noul` has no separate confidence field. |
| `Choice` | selected `choice`, its probability, and `confidence` | one declared label | Multi-class Brier/log loss use the full normalized distribution; top-label ECE uses probability of the selected label | `confidence` is descriptive provider output, not a substitute for probability. |
| `Score` | numeric `score`, ordered `legend`, distribution, and `confidence` | one declared level | Brier/log loss use the level distribution; top-label ECE uses the probability of the derived predicted level | `score` is an ordered position and may be between levels; it is not a boolean probability. |

For `Score`, the common domain model preserves the provider's raw score and
legend. It derives `predicted_level` as the nearest declared level; an exact
halfway tie chooses the lower level. This makes policy and accuracy results
reproducible without assuming whether a provider numbers its legend from zero
or one.

Before calibration, a `Choice` or `Score` distribution must contain every
declared label/level, have finite values in `[0,1]`, and sum to one within the
documented numeric tolerance. An invalid distribution is a provider-result
validation failure, not an incorrect prediction.

## Policy semantics

1. Validate the contract before any provider call.
2. Evaluate all V0 questions that share a state in one provider request where
   the provider supports it.
3. Validate and map typed provider output into the common domain model.
4. Evaluate policy rules in declared order. The first matching rule wins.
5. If no rule matches, return the required documented default outcome.

| Outcome | Meaning |
| --- | --- |
| `ACT` | The deterministic contract allows the bounded downstream action. V0 has no irreversible action executor. |
| `REVIEW` | The result needs a human or another explicitly configured workflow. V0 only records this disposition. |
| `FALLBACK` | The contract did not permit an automated action; the caller uses its safe default. |

A provider failure, timeout, malformed output, or missing answer is **not** a
policy outcome. It is recorded as an execution failure and returned through
the caller's error path. A caller may independently choose a safe fallback,
but that decision must be observable as caller behaviour rather than being
misreported as model policy.

## Failure taxonomy and denominators

| Category | Example | Counts as incorrect prediction? | Can policy run? |
| --- | --- | ---: | ---: |
| Contract validation failure | Unknown primitive or dangling rule reference | No | No |
| Dataset validation failure | Duplicate case ID or unknown expected label | No | No |
| Provider execution failure | Timeout, rate limit, authentication, transport error | No | No |
| Provider-result validation failure | Missing answer or invalid distribution | No | No |
| Decision-quality failure | Valid answer differs from labelled truth | Yes | Yes |
| Policy validation failure | Conflicting or invalid threshold | No | No |
| Persistence failure | Required audit record cannot be written | No | No new successful run is acknowledged |
| Telemetry export failure | Metrics backend unavailable | No | Yes; log locally without state/secret payloads |

Evaluation reports must show, separately: total dataset cases, provider
successes, provider failures, scored answers, unscored answers, and policy
outcome rates. Quality denominators include only valid scored answers; provider
failures are displayed beside them and cannot disappear into accuracy.

## Metric contract

- **Classification:** accuracy, precision, recall, and F1 are reported per
  applicable primitive and aggregation rule; binary and multi-class metrics are
  never mixed without a declared mapping.
- **Probabilistic:** Brier score and log loss use the probability distribution
  appropriate to the primitive. ECE uses configurable fixed buckets and
  reports bucket sample counts.
- **Selective automation:** report `ACT` coverage, `ACT` subset accuracy,
  `REVIEW` rate, `FALLBACK` rate, and provider error rate separately.
- **Operational:** report provider success/error counts and latency p50/p95
  from successful calls; failed-call latency is also retained for operations.
- **Small samples:** every report includes its denominator. Zero denominators
  produce `null`/not-applicable, never NaN, infinity, or a made-up percentage.

## Metadata, privacy, and security boundary

Persist only the minimum auditable metadata: run ID, contract fingerprint and
version, dataset/case ID where applicable, provider name, requested model,
SDK/adapter versions, request ID when supplied, timestamps, monotonic latency,
typed answers, policy explanation, and sanitized failure category.

Never persist, log, trace, or label a metric with:

- `TYPESAFE_API_KEY`, an LLM API key, authorization header, cookie, or token;
- raw input state by default;
- raw provider response/debug bodies by default;
- question text, user identifiers, case state, or high-cardinality IDs as
  Prometheus labels.

TypeSafe SDK debug logging remains disabled by default because its documented
debug mode logs request and response bodies without body redaction. The shadow
adapter's `debug`/attempt history is likewise treated as raw sensitive data.

## JDO-2 completion evidence

- [x] V0 scope and non-goals are bounded.
- [x] `Noul`, `Choice`, and `Score` evaluation semantics are explicit.
- [x] Probability and confidence are not conflated.
- [x] Provider/execution failures are separate from quality failures.
- [x] Contract, dataset, model, and provider metadata capture is defined.
- [x] Dependency/runtime pin policy is established.
- [x] Eight ADRs are accepted below `docs/adr/`.

The remaining work begins at **JDO-3 — Repository foundation and shared domain
model**. No provider call, database migration, API endpoint, or live secret has
been added by this documentation story.
