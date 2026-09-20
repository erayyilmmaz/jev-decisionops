from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from decisionops.api.app import create_app
from decisionops.application import DecisionOpsApplicationService
from decisionops.cli.main import (
    EXIT_INCOMPARABLE_EVALUATION,
    EXIT_PROVIDER_OR_EVALUATION_FAILURE,
    EXIT_QUALITY_GATE_FAILED,
    EXIT_SUCCESS,
    EXIT_USAGE_OR_CONFIGURATION_ERROR,
    run_cli,
)
from decisionops.config import Settings
from decisionops.contracts import load_contract
from decisionops.contracts.yaml_loader import load_yaml
from decisionops.evaluation import ReplayEngine, validate_dataset_data
from decisionops.models import (
    ChoiceAnswer,
    NoulAnswer,
    ProviderFailure,
    ProviderFailureKind,
    ProviderMetadata,
    ProviderResult,
    ReplayRunArtifact,
    ScoreAnswer,
    ValidatedDataset,
)
from decisionops.providers import ProviderRequest

REPOSITORY_ROOT = Path(__file__).parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "support-ticket-triage.yaml"
DATASET_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "datasets" / "support-ticket-triage-v1.yaml"


class FixedIdentifiers:
    def __init__(self, *identifiers: UUID) -> None:
        self._identifiers = list(identifiers)

    def new(self) -> UUID:
        return self._identifiers.pop(0)


class DeliveryFixtureProvider:
    def __init__(self, *, fail_case: str | None = None, incorrect_case: str | None = None) -> None:
        self.fail_case = fail_case
        self.incorrect_case = incorrect_case
        self.calls = 0

    async def evaluate(self, request: ProviderRequest) -> ProviderResult | ProviderFailure:
        self.calls += 1
        case_id = _case_id(request)
        if case_id == self.fail_case:
            return ProviderFailure(
                provider="delivery-fixture",
                kind=ProviderFailureKind.TIMEOUT,
                message="synthetic timeout",
            )
        return _provider_result(case_id, incorrect=case_id == self.incorrect_case)


def test_api_exposes_shared_decision_and_evaluation_services() -> None:
    provider = DeliveryFixtureProvider()
    service = _service(provider)
    app = create_app(
        Settings(live_provider_calls_enabled=True),
        service_factory=lambda _: service,
    )
    contract = _contract_data()
    dataset = _dataset_data()

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")
        decision = client.post(
            "/v1/decisions",
            headers={"X-Correlation-ID": "00000000-0000-0000-0000-000000000101"},
            json={"contract": contract, "state": {"message": "duplicate charge"}},
        )
        evaluation = client.post(
            "/v1/evaluations",
            json={"contract": contract, "dataset": dataset},
        )

        assert health.status_code == 200
        assert ready.json()["status"] == "ready"
        assert decision.status_code == 200
        assert decision.headers["X-Correlation-ID"] == "00000000-0000-0000-0000-000000000101"
        assert decision.headers["X-Decision-Run-ID"] == decision.json()["run_id"]
        assert "message" not in decision.text
        assert evaluation.status_code == 201
        evaluation_id = evaluation.headers["X-Evaluation-ID"]
        assert client.get(f"/v1/evaluations/{evaluation_id}").json() == evaluation.json()
        assert client.get(f"/v1/evaluations/{evaluation_id}/report").status_code == 200
        assert "/v1/decisions" in client.get("/openapi.json").json()["paths"]
    assert provider.calls == 5


def test_api_rejects_invalid_contract_before_provider_and_maps_safe_failures() -> None:
    provider = DeliveryFixtureProvider()
    service = _service(provider, provider_execution_enabled=False)
    app = create_app(
        Settings(live_provider_calls_enabled=True),
        service_factory=lambda _: service,
    )
    invalid_contract = _contract_data()
    invalid_contract["questions"] = {}

    with TestClient(app) as client:
        invalid = client.post(
            "/v1/decisions",
            json={"contract": invalid_contract, "state": {"message": "duplicate charge"}},
        )
        missing = client.get("/v1/evaluations/00000000-0000-0000-0000-000000000199")

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_contract"
    assert provider.calls == 0
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "evaluation_not_found"


def test_api_enforces_request_limits_and_disabled_or_unready_dependencies() -> None:
    provider = DeliveryFixtureProvider()
    service = _service(provider, provider_execution_enabled=False)
    app = create_app(
        Settings(live_provider_calls_enabled=False, api_max_request_bytes=1024),
        service_factory=lambda _: service,
        readiness_check=_not_ready,
    )

    with TestClient(app) as client:
        disabled = client.post(
            "/v1/decisions",
            json={"contract": _contract_data(), "state": {"message": "duplicate charge"}},
        )
        oversized = client.post(
            "/v1/decisions",
            json={"contract": _contract_data(), "state": {"message": "x" * 2_000}},
        )
        not_ready = client.get("/ready")

    assert disabled.status_code == 503
    assert disabled.json()["error"]["code"] == "provider_execution_disabled"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request_too_large"
    assert not_ready.status_code == 503
    assert provider.calls == 0


def test_cli_validates_contract_and_uses_quiet_machine_readable_output() -> None:
    stdout = StringIO()
    assert run_cli(["contract", "validate", str(CONTRACT_PATH), "--json"], stdout=stdout) == 0
    assert json.loads(stdout.getvalue())["name"] == "support-ticket-triage"

    quiet_stdout = StringIO()
    assert (
        run_cli(
            ["contract", "validate", str(CONTRACT_PATH), "--quiet"],
            stdout=quiet_stdout,
        )
        == EXIT_SUCCESS
    )
    assert quiet_stdout.getvalue() == ""


def test_cli_exit_codes_cover_validation_provider_quality_and_incomparable(tmp_path: Path) -> None:
    input_path = tmp_path / "ticket.json"
    input_path.write_text(json.dumps({"message": "duplicate charge"}), encoding="utf-8")
    invalid_contract = tmp_path / "invalid.yaml"
    invalid_contract.write_text("version: 1\nname: invalid\nquestions: {}\n", encoding="utf-8")
    provider = DeliveryFixtureProvider(fail_case="refund-clear-001")
    service = _service(provider)

    assert (
        run_cli(
            ["run", "--contract", str(invalid_contract), "--input", str(input_path), "--quiet"],
            service=service,
        )
        == EXIT_USAGE_OR_CONFIGURATION_ERROR
    )
    assert provider.calls == 0
    assert (
        run_cli(
            ["eval", "--contract", str(CONTRACT_PATH), "--dataset", str(DATASET_PATH), "--quiet"],
            service=service,
        )
        == EXIT_PROVIDER_OR_EVALUATION_FAILURE
    )

    baseline, current = _complete_artifacts()
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    thresholds_path = tmp_path / "thresholds.json"
    baseline_path.write_text(baseline.model_dump_json(), encoding="utf-8")
    current_path.write_text(current.model_dump_json(), encoding="utf-8")
    thresholds_path.write_text(
        json.dumps({"version": 1, "minimum_accuracy": 1.0}), encoding="utf-8"
    )
    assert (
        run_cli(
            [
                "compare",
                "--baseline",
                str(baseline_path),
                "--current",
                str(current_path),
                "--thresholds",
                str(thresholds_path),
                "--quiet",
            ]
        )
        == EXIT_QUALITY_GATE_FAILED
    )

    current_path.write_text(
        current.model_copy(update={"dataset_fingerprint": "0" * 64}).model_dump_json(),
        encoding="utf-8",
    )
    assert (
        run_cli(
            [
                "compare",
                "--baseline",
                str(baseline_path),
                "--current",
                str(current_path),
                "--quiet",
            ]
        )
        == EXIT_INCOMPARABLE_EVALUATION
    )


async def _not_ready() -> bool:
    return False


def _service(
    provider: DeliveryFixtureProvider,
    *,
    provider_execution_enabled: bool = True,
) -> DecisionOpsApplicationService:
    return DecisionOpsApplicationService(
        provider=provider,
        provider_execution_enabled=provider_execution_enabled,
        replay_engine=ReplayEngine(
            identifiers=FixedIdentifiers(UUID("00000000-0000-0000-0000-000000000111"))
        ),
        identifiers=FixedIdentifiers(UUID("00000000-0000-0000-0000-000000000112")),
    )


def _complete_artifacts() -> tuple[ReplayRunArtifact, ReplayRunArtifact]:
    contract = load_contract(CONTRACT_PATH)
    dataset = _dataset_data()
    validated_dataset: ValidatedDataset = validate_dataset_data(dataset, contract)
    baseline = asyncio.run(
        ReplayEngine(
            identifiers=FixedIdentifiers(UUID("00000000-0000-0000-0000-000000000121"))
        ).run(
            provider=DeliveryFixtureProvider(),
            contract=contract,
            dataset=validated_dataset,
        )
    )
    current = asyncio.run(
        ReplayEngine(
            identifiers=FixedIdentifiers(UUID("00000000-0000-0000-0000-000000000122"))
        ).run(
            provider=DeliveryFixtureProvider(incorrect_case="cancellation-normal-003"),
            contract=contract,
            dataset=validated_dataset,
        )
    )
    return baseline, current


def _contract_data() -> dict[str, object]:
    data = load_yaml(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _dataset_data() -> dict[str, object]:
    data = load_yaml(DATASET_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _case_id(request: ProviderRequest) -> str:
    message = request.state["message"]
    assert isinstance(message, str)
    markers = {
        "duplicate charge": "refund-clear-001",
        "accessed by someone else": "unauthorized-critical-002",
        "cancel my subscription": "cancellation-normal-003",
        "current plan information": "information-low-004",
    }
    return next(case_id for marker, case_id in markers.items() if marker in message)


def _provider_result(case_id: str, *, incorrect: bool = False) -> ProviderResult:
    expected: dict[str, tuple[str, float, int]] = {
        "refund-clear-001": ("refund", 0.05, 0),
        "unauthorized-critical-002": ("support", 0.95, 3),
        "cancellation-normal-003": ("cancellation", 0.05, 1),
        "information-low-004": ("information", 0.05, 0),
    }
    intent, noul, urgency = expected[case_id]
    if incorrect:
        intent = "support"
    return ProviderResult(
        metadata=ProviderMetadata(provider="delivery-fixture", requested_model="fixture-v1"),
        latency_ms=12,
        answers=(
            ChoiceAnswer(
                question_id="intent",
                choice=intent,
                probabilities={
                    option: 0.97 if option == intent else 0.01
                    for option in ("refund", "cancellation", "support", "information")
                },
                confidence=0.99,
            ),
            NoulAnswer(question_id="unauthorized_activity", noul=noul),
            ScoreAnswer(
                question_id="urgency",
                score=float(urgency),
                legend={0: "low", 1: "medium", 2: "high", 3: "critical"},
                probabilities={score: 0.97 if score == urgency else 0.01 for score in range(4)},
                confidence=0.99,
            ),
        ),
    )
