# JDO-6 — Deterministic confidence-aware Policy Engine

## Purpose

The policy engine combines a validated contract with a valid typed
`ProviderResult`. It never calls Jev, mutates provider output, or converts a
provider execution failure into an `ACT`, `REVIEW`, or `FALLBACK` outcome.

## Evaluation rules

1. Rules are evaluated in their YAML declaration order.
2. Every predicate inside one rule is combined with logical **AND**.
3. The first matching rule wins.
4. If no rule matches, the contract's required `policy.default` is returned.
5. The result includes one `RuleEvaluation` for each rule inspected up to the
   match, including observed value, threshold/label, and match result.

This means rule ordering is an explicit product decision, not incidental code
behaviour. A provider failure is outside this function's input type and must be
handled by the caller as an execution failure.

## Example

For this answer:

```text
unauthorized_activity.noul = 0.94
```

and this rule:

```yaml
when:
  question: unauthorized_activity
  probability_gte: 0.90
outcome: review
```

the outcome is `review`; the trace records observed `0.94`, expected `0.90`,
and `matched=true`. At `0.89`, that rule does not match. Boundary comparisons
are therefore reproducible: `_gte` is inclusive and `_lt` is exclusive.

## Supported V1 predicate mapping

| Typed answer | Policy predicates |
| --- | --- |
| `NoulAnswer.noul` | `probability_gte`, `probability_lt` |
| `ChoiceAnswer.choice` / `.confidence` | `equals`, `not_equals`, `confidence_gte`, `confidence_lt` |
| `ScoreAnswer.score` / `.confidence` | `score_gte`, `score_lt`, `confidence_gte`, `confidence_lt` |

Contract validation rejects incompatible question/predicate combinations before
the engine runs. The engine repeats type checks defensively in case a caller
constructs an invalid result in memory.

## Safe boundary

The trace contains only question IDs, rule IDs, predicate names, numeric
values, and declared labels. It contains no raw customer state, API key,
provider debug body, or policy-side AI output.
