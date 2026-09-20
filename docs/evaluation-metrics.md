# JDO-9 — Evaluation metrics and calibration engine

## Purpose

`MetricsEngine` compares one terminal provider state for every labelled case; it is fully offline and makes no provider, database, clock, or API call.

`report_json(...)` serializes reports with sorted mapping keys. Artifacts retain fingerprints and aggregate counts, never raw state, messages, secrets, or provider bodies.

## Denominators

Each `CaseEvaluation` has exactly one terminal state: `ProviderResult` or sanitized `ProviderFailure`. Missing, duplicate, or unknown case IDs are rejected before scoring.

| Input | Quality behavior |
| --- | --- |
| Valid answer | Included in the question quality denominator. |
| Missing, duplicate, wrong-kind, or invalid distribution | Unscored, never silently wrong. |
| Provider failure | Reported separately; every question in the case is unscored. |

Every metric has an explicit denominator. Zero-case metrics use `value: null`, never `NaN`, infinity, or a fabricated percentage.

## Formulas

Question reports have a truth-row/prediction-column confusion matrix, accuracy, and macro precision/recall/F1. Macro denominators are applicable class counts; accuracy uses scored answers.

| Primitive | Ground truth and prediction | Brier / log loss | ECE input |
| --- | --- | --- | --- |
| Noul | Boolean; `true` when `noul >= 0.5` | `(p-y)^2`; binary NLL | event probability and boolean frequency |
| Choice | Declared label; `choice` | one-hot squared-error sum; `-ln(P(true))` | `P(choice)` and correctness |
| Score | Declared level; nearest legend level | one-hot squared-error sum; `-ln(P(true))` | derived-level probability and correctness |

An exact Score halfway tie chooses the lower legend level. Choice/Score distributions must match declared labels/levels, be finite in `[0,1]`, and sum to one within `1e-9`; otherwise they are unscored. Valid log-loss input is clipped only to `[1e-15, 1 - 1e-15]`.

There is no cross-primitive Brier or ECE aggregate: binary event and multi-class top-label calibration mean different things. `overall_accuracy` aggregates all scored question predictions only.

## Calibration and confidence

`calibration_bucket_count` defaults to `10` and accepts `2` through `100`. Buckets are equal-width on `[0,1]`, lower-inclusive and upper-exclusive except the final bucket, which includes `1`. Each exposes sample count, mean prediction, and observed frequency. ECE is the sample-weighted absolute gap.

Choice and Score have distinct confidence buckets using provider confidence and selected-label correctness. Confidence is descriptive, not an ECE input and not a replacement for probability. Noul has no confidence bucket.

## Automation and operations

`act_coverage`, `review_rate`, `fallback_rate`, and `provider_error_rate` use all dataset cases. `act_subset_accuracy` uses only fully scored ACT cases; `act_unscored_cases` remains separate. `selective_risk` is `1 - act_subset_accuracy`.

Provider successes/failures remain separate. Successful latency p50/p95 uses nearest rank: sorted index `ceil(q × n) - 1`; failures do not enter percentiles.

## Programmatic use

```python
from decisionops.evaluation import MetricsEngine, report_json

report = MetricsEngine(calibration_bucket_count=10).evaluate(
    contract,
    dataset,
    case_evaluations,
)
artifact = report_json(report)
```

The later replay/regression story obtains `case_evaluations`. JDO-9 scores only existing sanitized terminal records and does not change production policy.
