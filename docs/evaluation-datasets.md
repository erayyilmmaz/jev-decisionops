# JDO-8 — Labelled evaluation dataset format and fixture suite

## Purpose

An evaluation dataset is a versioned, ordered collection of **synthetic**
states and ground-truth labels. It is evaluated offline in later stories; it
does not call Jev, a shadow provider, PostgreSQL, or an API endpoint.

Each dataset is bound to one exact validated Decision Contract by its name,
version, and contract fingerprint. This prevents an old label set from being
silently evaluated against changed questions or criteria.

## V1 YAML shape

```yaml
version: 1
name: support-ticket-triage-synthetic-v1
contract:
  name: support-ticket-triage
  version: 1
  fingerprint: <64-character-contract-sha256>
cases:
  - case_id: refund-clear-001
    state:
      message: Synthetic fixture: I would like a refund.
    labels:
      intent: refund
      unauthorized_activity: false
      urgency: low
```

| Field | Requirement |
| --- | --- |
| `version` | Required and exactly `1`. It participates in the fingerprint. |
| `contract` | Its name, version, and fingerprint must each exactly match the loaded contract. |
| `cases` | Non-empty ordered list with unique `case_id` values. Case order participates in the fingerprint. |
| `state` | JSON-compatible synthetic input data for a later provider evaluation. |
| `labels` | Must contain exactly one ground-truth value for every contract question. |
| Noul label | YAML boolean `true` or `false`; never a confidence value or a quoted string. |
| Choice label | One declared Choice criterion label. |
| Score label | One declared ordered Score criterion level. |

The canonical dataset JSON sorts mapping keys but preserves the `cases` list
order. Therefore YAML formatting and mapping-key order do not affect the
fingerprint, while changing the fixture order, labels, state, contract identity,
or schema version does.

## Fixture suite

The checked-in fixture
[`support-ticket-triage-v1.yaml`](../tests/fixtures/datasets/support-ticket-triage-v1.yaml)
contains only synthetic support-ticket examples. It covers refund, cancellation,
support, and information Choice labels; both Noul truth values; and low,
medium, and critical Score labels. Future datasets should add deliberately
labelled cases rather than reusing customer input.

## Local validation

```python
from pathlib import Path

from decisionops.contracts import load_contract
from decisionops.evaluation import load_dataset

contract = load_contract(Path("contracts/support-ticket-triage.yaml"))
dataset = load_dataset(
    Path("tests/fixtures/datasets/support-ticket-triage-v1.yaml"),
    contract,
)
print(dataset.fingerprint)
```

Expected result: a 64-character SHA-256 fingerprint. Invalid YAML, duplicate
keys, wrong contract identity, duplicate case IDs, missing/unknown questions,
and invalid label types/values raise `DatasetValidationError` with stable
issue paths and codes.

## Security boundary

Datasets contain raw state, so V0 repository fixtures are synthetic only. Do
not add production messages, customer identifiers, secrets, credentials,
authorization headers, provider response bodies, or debug history. JDO-7 audit
persistence intentionally does not persist this state, and JDO-8 does not
change that boundary.
