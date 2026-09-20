"""Versioned labelled dataset loading, contract binding, and fingerprinting."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import yaml
from pydantic import ValidationError

from decisionops.contracts.schema import (
    ChoiceQuestion,
    NoulQuestion,
    ScoreQuestion,
    ValidatedContract,
)
from decisionops.contracts.yaml_loader import DuplicateYamlKeyError, load_yaml
from decisionops.evaluation.errors import DatasetIssue, DatasetValidationError
from decisionops.models import EvaluationDataset, JsonValue, ValidatedDataset


def load_dataset(path: Path, contract: ValidatedContract) -> ValidatedDataset:
    """Load and validate one synthetic YAML dataset without external calls."""

    try:
        yaml_text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise DatasetValidationError(
            (
                DatasetIssue(
                    path=str(path),
                    message=f"cannot read dataset: {error.strerror or str(error)}",
                    code="file_read_error",
                ),
            )
        ) from error

    try:
        data = load_yaml(yaml_text)
    except DuplicateYamlKeyError as error:
        raise DatasetValidationError(
            (
                DatasetIssue(
                    path=f"line {error.line}, column {error.column}",
                    message=f"duplicate YAML key {error.key!r}",
                    code="duplicate_key",
                ),
            )
        ) from error
    except yaml.YAMLError as error:
        raise DatasetValidationError(
            (
                DatasetIssue(
                    path=str(path),
                    message=f"malformed YAML: {error}",
                    code="malformed_yaml",
                ),
            )
        ) from error

    return validate_dataset_data(data, contract)


def validate_dataset_data(data: object, contract: ValidatedContract) -> ValidatedDataset:
    """Validate dataset shape and labels against one exact Decision Contract."""

    try:
        dataset = EvaluationDataset.model_validate(data)
    except ValidationError as error:
        raise DatasetValidationError(_pydantic_issues(error)) from error

    issues = _contract_binding_issues(dataset, contract)
    issues.extend(_case_issues(dataset, contract))
    if issues:
        raise DatasetValidationError(tuple(issues))

    representation = canonical_dataset_json(dataset)
    return ValidatedDataset(
        dataset=dataset,
        canonical_json=representation,
        fingerprint=fingerprint_dataset(dataset),
    )


def canonical_dataset_json(dataset: EvaluationDataset) -> str:
    """Return canonical JSON; list order intentionally preserves case order."""

    return json.dumps(
        dataset.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_dataset(dataset: EvaluationDataset) -> str:
    """Return SHA-256 identity of the schema version and ordered labelled cases."""

    return sha256(canonical_dataset_json(dataset).encode("utf-8")).hexdigest()


def _contract_binding_issues(
    dataset: EvaluationDataset,
    contract: ValidatedContract,
) -> list[DatasetIssue]:
    expected = contract.contract
    reference = dataset.contract
    actual_values = (reference.name, reference.version, reference.fingerprint)
    expected_values = (expected.name, expected.version, contract.fingerprint)
    field_names = ("name", "version", "fingerprint")
    return [
        DatasetIssue(
            path=f"contract.{field_name}",
            message=f"must match loaded contract {expected_value!r}",
            code="contract_mismatch",
        )
        for field_name, actual_value, expected_value in zip(
            field_names, actual_values, expected_values, strict=True
        )
        if actual_value != expected_value
    ]


def _case_issues(dataset: EvaluationDataset, contract: ValidatedContract) -> list[DatasetIssue]:
    issues: list[DatasetIssue] = []
    seen_case_ids: set[str] = set()
    question_ids = set(contract.contract.questions)

    for case_index, case in enumerate(dataset.cases):
        case_path = f"cases.{case_index}"
        if case.case_id in seen_case_ids:
            issues.append(
                DatasetIssue(
                    path=f"{case_path}.case_id",
                    message=f"duplicate case ID {case.case_id!r}",
                    code="duplicate_case_id",
                )
            )
        seen_case_ids.add(case.case_id)

        label_ids = set(case.labels)
        for question_id in sorted(question_ids - label_ids):
            issues.append(
                DatasetIssue(
                    path=f"{case_path}.labels",
                    message=f"missing ground-truth label for question {question_id!r}",
                    code="missing_label",
                )
            )
        for question_id in sorted(label_ids - question_ids):
            issues.append(
                DatasetIssue(
                    path=f"{case_path}.labels.{question_id}",
                    message="label references an unknown contract question",
                    code="unknown_question",
                )
            )
        for question_id in sorted(label_ids & question_ids):
            issues.extend(
                _label_issues(
                    path=f"{case_path}.labels.{question_id}",
                    question_id=question_id,
                    label=case.labels[question_id],
                    contract=contract,
                )
            )

    return issues


def _label_issues(
    *,
    path: str,
    question_id: str,
    label: JsonValue,
    contract: ValidatedContract,
) -> list[DatasetIssue]:
    question = contract.contract.questions[question_id]
    if isinstance(question, NoulQuestion):
        if type(label) is bool:
            return []
        return [
            DatasetIssue(
                path=path,
                message="Noul ground truth must be boolean true or false",
                code="invalid_noul_label",
            )
        ]
    if isinstance(question, ChoiceQuestion):
        allowed_labels = set(question.criteria)
        label_kind = "Choice"
    elif isinstance(question, ScoreQuestion):
        allowed_labels = set(question.criteria)
        label_kind = "Score"
    else:
        return [
            DatasetIssue(
                path=path, message="unsupported contract question", code="unsupported_question"
            )
        ]

    if not isinstance(label, str) or label not in allowed_labels:
        return [
            DatasetIssue(
                path=path,
                message=f"{label_kind} ground truth must be one of {sorted(allowed_labels)!r}",
                code="invalid_label",
            )
        ]
    return []


def _pydantic_issues(error: ValidationError) -> tuple[DatasetIssue, ...]:
    """Convert Pydantic locations to stable field paths for delivery surfaces."""

    issues: list[DatasetIssue] = []
    for detail in error.errors(include_url=False):
        location = detail["loc"]
        path = ".".join(str(segment) for segment in location) or "$"
        issues.append(DatasetIssue(path=path, message=detail["msg"], code=detail["type"]))
    return tuple(issues)
