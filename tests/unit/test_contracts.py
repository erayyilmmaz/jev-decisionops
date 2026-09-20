from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from decisionops.contracts import ContractValidationError, load_contract, validate_contract_data

REPOSITORY_ROOT = Path(__file__).parents[2]
EXAMPLE_CONTRACT = REPOSITORY_ROOT / "contracts" / "support-ticket-triage.yaml"
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "contracts"


def valid_contract_data() -> dict[str, Any]:
    return {
        "version": 1,
        "name": "support-ticket-triage",
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "What does the customer want?",
                "criteria": {"refund": "Customer asks for money back.", "support": "Needs help."},
            },
            "unauthorized_activity": {
                "type": "noul",
                "instructions": "Is there evidence of unauthorized activity?",
            },
            "urgency": {
                "type": "score",
                "instructions": "How urgent is this?",
                "criteria": ["low", "high"],
            },
        },
        "policy": {
            "rules": [
                {
                    "when": {
                        "question": "intent",
                        "equals": "refund",
                        "confidence_gte": 0.95,
                    },
                    "outcome": "act",
                }
            ],
            "default": "fallback",
        },
    }


def test_example_contract_validates_and_has_a_fingerprint() -> None:
    validated = load_contract(EXAMPLE_CONTRACT)

    assert validated.contract.version == 1
    assert validated.contract.policy.rules[0].id == "suspicious-activity-review"
    assert len(validated.fingerprint) == 64


def test_implicit_rule_id_is_deterministic() -> None:
    validated = validate_contract_data(valid_contract_data())

    assert validated.contract.policy.rules[0].id == "rule-1"


def test_equivalent_mapping_order_has_the_same_fingerprint() -> None:
    first = valid_contract_data()
    second = deepcopy(first)
    second["questions"] = dict(reversed(list(second["questions"].items())))

    assert validate_contract_data(first).fingerprint == validate_contract_data(second).fingerprint


@pytest.mark.parametrize(
    ("fixture_name", "code"),
    [
        ("duplicate-key.yaml", "duplicate_key"),
        ("malformed.yaml", "malformed_yaml"),
    ],
)
def test_yaml_errors_are_actionable(fixture_name: str, code: str) -> None:
    with pytest.raises(ContractValidationError) as raised:
        load_contract(FIXTURES / fixture_name)

    assert raised.value.issues[0].code == code


def test_unknown_question_type_is_rejected() -> None:
    data = valid_contract_data()
    data["questions"]["intent"]["type"] = "freeform"

    with pytest.raises(ContractValidationError, match="union_tag_invalid"):
        validate_contract_data(data)


def test_policy_rejects_unknown_question_reference() -> None:
    data = valid_contract_data()
    data["policy"]["rules"][0]["when"]["question"] = "unknown_question"

    with pytest.raises(ContractValidationError, match="unknown question"):
        validate_contract_data(data)


def test_policy_rejects_out_of_range_threshold() -> None:
    data = valid_contract_data()
    data["policy"]["rules"][0]["when"] = {
        "question": "unauthorized_activity",
        "probability_gte": 1.01,
    }

    with pytest.raises(ContractValidationError, match=r"within \[0, 1\]"):
        validate_contract_data(data)


def test_policy_rejects_unknown_choice_label() -> None:
    data = valid_contract_data()
    data["policy"]["rules"][0]["when"]["equals"] = "cancellation"

    with pytest.raises(ContractValidationError, match="unknown Choice label"):
        validate_contract_data(data)


def test_contract_rejects_unsupported_executable_field() -> None:
    data = valid_contract_data()
    data["python"] = "__import__('os').system('echo unsafe')"

    with pytest.raises(ContractValidationError, match="extra_forbidden"):
        validate_contract_data(data)
