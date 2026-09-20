from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from decisionops.contracts import load_contract
from decisionops.contracts.schema import ValidatedContract
from decisionops.evaluation import (
    DatasetValidationError,
    canonical_dataset_json,
    fingerprint_dataset,
    load_dataset,
    validate_dataset_data,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "support-ticket-triage.yaml"
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "datasets"


def loaded_contract() -> ValidatedContract:
    return load_contract(CONTRACT_PATH)


def valid_dataset_data() -> dict[str, Any]:
    contract = loaded_contract()
    return {
        "version": 1,
        "name": "unit-dataset-v1",
        "contract": {
            "name": contract.contract.name,
            "version": contract.contract.version,
            "fingerprint": contract.fingerprint,
        },
        "cases": [
            {
                "case_id": "refund-001",
                "state": {"message": "Synthetic refund request", "account_status": "active"},
                "labels": {
                    "intent": "refund",
                    "unauthorized_activity": False,
                    "urgency": "low",
                },
            },
            {
                "case_id": "support-002",
                "state": {"message": "Synthetic support request", "account_status": "active"},
                "labels": {
                    "intent": "support",
                    "unauthorized_activity": True,
                    "urgency": "critical",
                },
            },
        ],
    }


def test_synthetic_fixture_loads_with_contract_binding_and_stable_identity() -> None:
    dataset = load_dataset(FIXTURES / "support-ticket-triage-v1.yaml", loaded_contract())

    assert dataset.dataset.name == "support-ticket-triage-synthetic-v1"
    assert [case.case_id for case in dataset.dataset.cases] == [
        "refund-clear-001",
        "unauthorized-critical-002",
        "cancellation-normal-003",
        "information-low-004",
    ]
    assert dataset.dataset.cases[1].labels["unauthorized_activity"] is True
    assert len(dataset.fingerprint) == 64
    assert dataset.canonical_json == canonical_dataset_json(dataset.dataset)
    assert dataset.fingerprint == fingerprint_dataset(dataset.dataset)


def test_mapping_key_order_does_not_change_dataset_fingerprint() -> None:
    first = valid_dataset_data()
    second = deepcopy(first)
    second["cases"][0]["state"] = dict(reversed(second["cases"][0]["state"].items()))

    assert (
        validate_dataset_data(first, loaded_contract()).fingerprint
        == validate_dataset_data(second, loaded_contract()).fingerprint
    )


def test_case_order_changes_dataset_fingerprint() -> None:
    first = valid_dataset_data()
    second = deepcopy(first)
    second["cases"].reverse()

    assert (
        validate_dataset_data(first, loaded_contract()).fingerprint
        != validate_dataset_data(second, loaded_contract()).fingerprint
    )


def test_dataset_rejects_contract_fingerprint_mismatch() -> None:
    data = valid_dataset_data()
    data["contract"]["fingerprint"] = "b" * 64

    with pytest.raises(DatasetValidationError, match="contract_mismatch"):
        validate_dataset_data(data, loaded_contract())


@pytest.mark.parametrize(
    ("question_id", "label", "code"),
    [
        ("unauthorized_activity", "true", "invalid_noul_label"),
        ("intent", "unknown", "invalid_label"),
        ("urgency", "urgent", "invalid_label"),
    ],
)
def test_dataset_rejects_incompatible_ground_truth_labels(
    question_id: str,
    label: object,
    code: str,
) -> None:
    data = valid_dataset_data()
    data["cases"][0]["labels"][question_id] = label

    with pytest.raises(DatasetValidationError, match=code):
        validate_dataset_data(data, loaded_contract())


def test_dataset_requires_every_contract_question_to_be_labelled() -> None:
    data = valid_dataset_data()
    del data["cases"][0]["labels"]["urgency"]

    with pytest.raises(DatasetValidationError, match="missing_label"):
        validate_dataset_data(data, loaded_contract())


def test_dataset_rejects_unknown_question_and_duplicate_case_id() -> None:
    data = valid_dataset_data()
    data["cases"][1]["case_id"] = "refund-001"
    data["cases"][0]["labels"]["unknown"] = "label"

    with pytest.raises(DatasetValidationError) as raised:
        validate_dataset_data(data, loaded_contract())

    assert {issue.code for issue in raised.value.issues} == {
        "duplicate_case_id",
        "unknown_question",
    }


def test_duplicate_yaml_keys_are_rejected_without_provider_call() -> None:
    with pytest.raises(DatasetValidationError) as raised:
        load_dataset(FIXTURES / "duplicate-key.yaml", loaded_contract())

    assert raised.value.issues[0].code == "duplicate_key"
