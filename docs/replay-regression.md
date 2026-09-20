# JDO-11 — Replay, baseline comparison, and regression engine

## Purpose

`ReplayEngine` runs a labelled dataset through one provider and the deterministic policy engine, then produces a `ReplayRunArtifact`. `RegressionEngine` compares two such artifacts and returns `PASS`, `FAIL`, or explicit `INCOMPARABLE`.

This is an offline evaluation path. It neither persists a decision nor changes the production policy path. A provider used for replay may be a fixture, the official Jev adapter, or the isolated shadow adapter; provider and model metadata remain visible in the artifact.

## Replay execution

Replay preserves the dataset's case order in its metric artifact while limiting provider calls with `max_concurrency`. The bound must be between `1` and `16` inclusive; the default is `4`.

For every dataset case, the engine performs this sequence:

1. build one validated `ProviderRequest` from the contract and case state;
2. call the provider behind the provider protocol;
3. map an unexpected adapter exception to a sanitized `ProviderFailure`;
4. evaluate the result with the deterministic policy engine; and
5. send terminal states to `MetricsEngine`.

Provider failures, invalid or incomplete answers, and policy evaluation failures make the artifact `PARTIAL`. Only a run with no provider failures, unscored answers, or policy errors is `COMPLETE`. Task cancellation is allowed to propagate; a cancelled replay does not manufacture a complete artifact.

The replay artifact includes its evaluation ID, status, contract and dataset fingerprints, distinct provider names, requested and resolved models, policy-error count, metrics, and a case-level quality summary. The summary contains only case ID, score counts, quality state, policy outcome, and failure kind. It never includes raw state, messages, API keys, provider response bodies, or adapter debug payloads.

## Compatibility and deltas

Comparison requires both artifacts to be `COMPLETE`, with equal contract fingerprints, dataset fingerprints, and case ID sets. A failed check returns `INCOMPARABLE` with one or more explicit reasons such as `current_run_is_partial`, `contract_fingerprint_mismatch`, or `dataset_fingerprint_mismatch`. No metric delta or quality gate is emitted for an incomparable pair.

`select_baseline(artifacts, evaluation_id=...)` chooses exactly one caller-named artifact from an in-memory or loaded collection. It rejects a missing or duplicate ID and never guesses based on recency, provider, or model. Compatibility remains the comparison engine's responsibility.

Provider or model changes are recorded as baseline/current metadata but are not themselves a compatibility failure. This allows an intentional Jev-versus-shadow or model-version experiment to be evaluated against the same contract and labels.

Comparable runs expose baseline, current, and `current - baseline` deltas for overall accuracy, ACT-subset accuracy, provider-error rate, and p95 latency. They also expose per-question accuracy, Brier-score, and ECE deltas. Changed cases are summarized without raw input using this order:

`provider_failure < unscored < incorrect < correct`

Moving upward is `IMPROVED`; moving downward is `REGRESSED`; an equal-rank state change is `CHANGED`.

## Versioned quality gates

`RegressionThresholds(version=1, ...)` applies limits to the current complete run only. Omit a limit to leave it unchecked. Available limits are:

| Limit | Pass condition |
| --- | --- |
| `minimum_accuracy` | Overall accuracy is at least the limit. |
| `maximum_ece` | Every applicable question ECE is at most the limit. |
| `maximum_brier_score` | Every question Brier score is at most the limit. |
| `minimum_act_accuracy` | ACT-subset accuracy is at least the limit. |
| `maximum_provider_error_rate` | Provider error rate is at most the limit. |
| `maximum_latency_p95_ms` | Successful-call p95 latency is at most the limit. |

An enabled limit whose metric is not applicable fails explicitly rather than silently passing. A comparable run is `FAIL` when any enabled check fails; otherwise it is `PASS`.

## Programmatic use

```python
from decisionops.evaluation import RegressionEngine, ReplayEngine
from decisionops.models import RegressionThresholds

baseline = await ReplayEngine().run(
    provider=baseline_provider,
    contract=contract,
    dataset=dataset,
)
current = await ReplayEngine(max_concurrency=4).run(
    provider=candidate_provider,
    contract=contract,
    dataset=dataset,
)
comparison = RegressionEngine().compare(
    baseline,
    current,
    thresholds=RegressionThresholds(minimum_accuracy=0.95),
)
```

`replay_artifact_json(...)` and `regression_comparison_json(...)` serialize these objects using sorted JSON mapping keys for CI artifacts. They are deterministic for the same domain objects and intentionally do not write a database record.
