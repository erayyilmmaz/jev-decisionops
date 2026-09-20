"""CI-friendly CLI built on the shared DecisionOps application service."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO, cast

from pydantic import ValidationError

from decisionops.application import (
    DatasetSizeLimitError,
    DecisionOpsApplicationService,
    DecisionPolicyExecutionError,
    OutboundProviderNotAllowedError,
    ProviderExecutionDisabledError,
)
from decisionops.config import Settings
from decisionops.contracts import ContractValidationError, load_contract
from decisionops.evaluation import DatasetValidationError
from decisionops.models import JsonValue, RegressionOutcome, RegressionThresholds, ReplayRunArtifact

EXIT_SUCCESS = 0
EXIT_QUALITY_GATE_FAILED = 1
EXIT_USAGE_OR_CONFIGURATION_ERROR = 2
EXIT_PROVIDER_OR_EVALUATION_FAILURE = 3
EXIT_INCOMPARABLE_EVALUATION = 4


class CliUsageError(ValueError):
    """A stable, safe error caused by CLI input or local configuration."""


def main() -> int:
    """Run the installed console command with process arguments."""

    return run_cli(sys.argv[1:])


def run_cli(
    argv: Sequence[str],
    *,
    service: DecisionOpsApplicationService | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run a command with stable automation exit codes and optional test injection."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else EXIT_USAGE_OR_CONFIGURATION_ERROR

    try:
        resolved_service = service or DecisionOpsApplicationService.from_settings(Settings())
        return _dispatch(args, resolved_service, output, errors)
    except (
        CliUsageError,
        ContractValidationError,
        DatasetValidationError,
        ValidationError,
    ) as error:
        _emit_error(args, error, errors)
        return EXIT_USAGE_OR_CONFIGURATION_ERROR
    except (
        ProviderExecutionDisabledError,
        OutboundProviderNotAllowedError,
        DecisionPolicyExecutionError,
        DatasetSizeLimitError,
    ) as error:
        _emit_error(args, error, errors)
        return EXIT_PROVIDER_OR_EVALUATION_FAILURE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decisionops", description="Jev DecisionOps developer CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    contract = commands.add_parser("contract", help="Decision Contract operations")
    contract_commands = contract.add_subparsers(dest="contract_command", required=True)
    validate = contract_commands.add_parser(
        "validate", help="validate a Decision Contract YAML file"
    )
    validate.add_argument("path", type=Path)
    _add_output_arguments(validate)

    run = commands.add_parser("run", help="execute one decision")
    run.add_argument("--contract", required=True, type=Path)
    run.add_argument("--input", required=True, type=Path)
    _add_output_arguments(run)

    evaluate = commands.add_parser("eval", help="run one labelled evaluation dataset")
    evaluate.add_argument("--contract", required=True, type=Path)
    evaluate.add_argument("--dataset", required=True, type=Path)
    _add_output_arguments(evaluate)

    compare = commands.add_parser("compare", help="compare two replay artifact JSON files")
    compare.add_argument("--baseline", required=True, type=Path)
    compare.add_argument("--current", required=True, type=Path)
    compare.add_argument("--thresholds", type=Path)
    _add_output_arguments(compare)
    return parser


def _add_output_arguments(parser: argparse.ArgumentParser) -> None:
    output_mode = parser.add_mutually_exclusive_group()
    output_mode.add_argument("--json", action="store_true", dest="json_output")
    output_mode.add_argument("--quiet", action="store_true")


def _dispatch(
    args: argparse.Namespace,
    service: DecisionOpsApplicationService,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if args.command == "contract":
        contract = load_contract(args.path)
        _emit(
            args,
            {
                "name": contract.contract.name,
                "version": contract.contract.version,
                "fingerprint": contract.fingerprint,
            },
            f"valid contract: {contract.contract.name} v{contract.contract.version}",
            stdout,
        )
        return EXIT_SUCCESS
    if args.command == "run":
        contract = load_contract(args.contract)
        state = _load_json_object(args.input, "input")
        decision_artifact = asyncio.run(
            service.execute_decision(
                contract_data=contract.contract.model_dump(mode="json"),
                state=cast(dict[str, JsonValue], state),
            )
        )
        _emit(
            args,
            decision_artifact.model_dump(mode="json"),
            f"decision run: {decision_artifact.run_id}",
            stdout,
        )
        return (
            EXIT_PROVIDER_OR_EVALUATION_FAILURE
            if decision_artifact.failure is not None
            else EXIT_SUCCESS
        )
    if args.command == "eval":
        contract = load_contract(args.contract)
        dataset = _load_json_or_yaml_object(args.dataset, "dataset")
        evaluation_artifact = asyncio.run(
            service.execute_evaluation(
                contract_data=contract.contract.model_dump(mode="json"),
                dataset_data=dataset,
            )
        )
        _emit(
            args,
            evaluation_artifact.model_dump(mode="json"),
            f"evaluation run: {evaluation_artifact.evaluation_id} ({evaluation_artifact.status})",
            stdout,
        )
        return (
            EXIT_PROVIDER_OR_EVALUATION_FAILURE
            if evaluation_artifact.status.value == "partial"
            else EXIT_SUCCESS
        )
    if args.command == "compare":
        baseline = _load_replay_artifact(args.baseline)
        current = _load_replay_artifact(args.current)
        thresholds = _load_thresholds(args.thresholds) if args.thresholds is not None else None
        comparison = service.compare_evaluations(baseline, current, thresholds=thresholds)
        _emit(
            args,
            comparison.model_dump(mode="json"),
            f"comparison result: {comparison.outcome}",
            stdout,
        )
        if comparison.outcome == RegressionOutcome.FAIL:
            return EXIT_QUALITY_GATE_FAILED
        if comparison.outcome == RegressionOutcome.INCOMPARABLE:
            return EXIT_INCOMPARABLE_EVALUATION
        return EXIT_SUCCESS
    raise CliUsageError("unknown command")


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliUsageError(f"cannot read valid JSON {label}") from error
    if not isinstance(value, dict):
        raise CliUsageError(f"{label} JSON must be an object")
    return value


def _load_json_or_yaml_object(path: Path, label: str) -> dict[str, object]:
    if path.suffix.lower() == ".json":
        return _load_json_object(path, label)
    try:
        from decisionops.contracts.yaml_loader import load_yaml

        value = load_yaml(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise CliUsageError(f"cannot read valid YAML {label}") from error
    if not isinstance(value, dict):
        raise CliUsageError(f"{label} YAML must be an object")
    return value


def _load_replay_artifact(path: Path) -> ReplayRunArtifact:
    try:
        return ReplayRunArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as error:
        raise CliUsageError("cannot read a valid replay artifact JSON file") from error


def _load_thresholds(path: Path) -> RegressionThresholds:
    try:
        return RegressionThresholds.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as error:
        raise CliUsageError("cannot read a valid regression thresholds JSON file") from error


def _emit(args: argparse.Namespace, payload: object, human: str, output: TextIO) -> None:
    if args.quiet:
        return
    if args.json_output:
        print(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            file=output,
        )
        return
    print(human, file=output)


def _emit_error(args: argparse.Namespace, error: Exception, stderr: TextIO) -> None:
    if args.quiet:
        return
    if args.json_output:
        print(
            json.dumps(
                {"error": {"message": _safe_error_message(error)}},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=stderr,
        )
        return
    print(f"error: {_safe_error_message(error)}", file=stderr)


def _safe_error_message(error: Exception) -> str:
    if isinstance(error, ContractValidationError):
        return "Decision Contract validation failed"
    if isinstance(error, DatasetValidationError):
        return "evaluation dataset validation failed"
    if isinstance(error, ValidationError):
        return "configuration validation failed"
    return str(error)
