"""Decision Contract loading, validation, canonicalization, and fingerprinting."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from decisionops.contracts.errors import ContractIssue, ContractValidationError
from decisionops.contracts.schema import DecisionContract, ValidatedContract, canonical_json
from decisionops.contracts.schema import fingerprint_contract as calculate_fingerprint
from decisionops.contracts.yaml_loader import DuplicateYamlKeyError, load_yaml


def load_contract(path: Path) -> ValidatedContract:
    """Load one YAML file and validate it without calling any provider."""

    try:
        yaml_text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractValidationError(
            (
                ContractIssue(
                    path=str(path),
                    message=f"cannot read contract: {error.strerror or str(error)}",
                    code="file_read_error",
                ),
            )
        ) from error

    try:
        data = load_yaml(yaml_text)
    except DuplicateYamlKeyError as error:
        raise ContractValidationError(
            (
                ContractIssue(
                    path=f"line {error.line}, column {error.column}",
                    message=f"duplicate YAML key {error.key!r}",
                    code="duplicate_key",
                ),
            )
        ) from error
    except yaml.YAMLError as error:
        raise ContractValidationError(
            (
                ContractIssue(
                    path=str(path),
                    message=f"malformed YAML: {error}",
                    code="malformed_yaml",
                ),
            )
        ) from error

    return validate_contract_data(data)


def validate_contract_data(data: object) -> ValidatedContract:
    """Validate parsed data and produce its stable V1 identity."""

    normalized_data = _assign_implicit_rule_ids(data)
    try:
        contract = DecisionContract.model_validate(normalized_data)
    except ValidationError as error:
        raise ContractValidationError(_pydantic_issues(error)) from error

    representation = canonical_json(contract)
    return ValidatedContract(
        contract=contract,
        canonical_json=representation,
        fingerprint=calculate_fingerprint(contract),
    )


def _assign_implicit_rule_ids(data: object) -> object:
    """Give legacy/example rules deterministic IDs without mutating caller data."""

    if not isinstance(data, Mapping):
        return data

    normalized: dict[object, object] = dict(data)
    policy = data.get("policy")
    if not isinstance(policy, Mapping):
        return normalized

    rules = policy.get("rules")
    if not isinstance(rules, list):
        return normalized

    normalized_policy: dict[object, object] = dict(policy)
    normalized_rules: list[object] = []
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, Mapping):
            normalized_rules.append(rule)
            continue
        normalized_rule: dict[object, object] = dict(rule)
        normalized_rule.setdefault("id", f"rule-{index}")
        normalized_rules.append(normalized_rule)

    normalized_policy["rules"] = normalized_rules
    normalized["policy"] = normalized_policy
    return normalized


def _pydantic_issues(error: ValidationError) -> tuple[ContractIssue, ...]:
    """Convert Pydantic locations to stable field paths suitable for CLI/API output."""

    issues: list[ContractIssue] = []
    for detail in error.errors(include_url=False):
        location = detail["loc"]
        path = ".".join(str(segment) for segment in location) or "$"
        issues.append(
            ContractIssue(
                path=path,
                message=detail["msg"],
                code=detail["type"],
            )
        )
    return tuple(issues)
