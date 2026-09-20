# JDO-4 — Versioned Decision Contract

## Purpose

A Decision Contract declares typed Jev questions and deterministic policy in
YAML. It is versioned data, not executable Python: loader validation happens
before any provider can be called.

## V1 shape

```yaml
version: 1
name: support-ticket-triage
questions:
  intent:
    type: choice
    instructions: What is the customer's primary intent?
    criteria:
      refund: Customer wants a refund.
      support: Customer needs technical assistance.
policy:
  rules:
    - when:
        question: intent
        equals: refund
        confidence_gte: 0.95
      outcome: act
  default: fallback
```

| Field | Rule |
| --- | --- |
| `version` | Required and currently exactly `1`. |
| `name` | Required lowercase slug using letters, digits, and hyphens. |
| `questions` | Non-empty mapping. Question IDs use lowercase letters, digits, and underscores. |
| `type` | Exactly `noul`, `choice`, or `score`. |
| `criteria` | Optional `true`/`false` descriptions for `noul`; at least two labelled options for `choice`; at least two unique ordered levels for `score`. |
| `policy.rules` | Non-empty ordered rules. `id` is optional; omitted IDs become `rule-1`, `rule-2`, and so on in canonical output. |
| `policy.default` | Required `act`, `review`, or `fallback`. |

## Predicate compatibility

| Question type | Supported predicates |
| --- | --- |
| `noul` | `probability_gte`, `probability_lt` |
| `choice` | `equals` or `not_equals`, optionally combined with `confidence_gte` / `confidence_lt` |
| `score` | `score_gte` / `score_lt`, optionally combined with `confidence_gte` / `confidence_lt` |

Thresholds for probability and confidence are within `[0, 1]`. A lower bound
must be strictly less than its upper bound. Every policy question reference and
Choice label is checked against the same contract.

## Local validation contract

Use the core API from Python now; the stable end-user CLI command belongs to
JDO-12.

```python
from pathlib import Path

from decisionops.contracts import load_contract

validated = load_contract(Path("contracts/support-ticket-triage.yaml"))
print(validated.fingerprint)
```

Expected result: a 64-character SHA-256 fingerprint. The canonical form sorts
object keys and removes YAML formatting differences, so semantically equivalent
contracts produce the same fingerprint.

## Safe failure behaviour

The loader rejects malformed YAML, duplicate YAML keys, unsupported fields,
unknown question types, invalid criteria, dangling policy references, wrong
question/predicate combinations, and invalid thresholds. `ContractValidationError`
contains `ContractIssue` entries with a field path, message, and code.

Validation loads no secret and makes no network, provider, database, telemetry,
or policy-execution call.
