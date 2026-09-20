from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from decisionops.application import (
    DatasetSizeLimitError,
    DecisionOpsApplicationService,
    OutboundProviderNotAllowedError,
)
from decisionops.contracts.yaml_loader import load_yaml
from decisionops.models import (
    PolicyOutcome,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResult,
    RegressionOutcome,
)
from decisionops.providers import ProviderRequest
from decisionops.telemetry import Telemetry, redact_fields

REPOSITORY_ROOT = Path(__file__).parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "support-ticket-triage.yaml"
DATASET_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "datasets" / "support-ticket-triage-v1.yaml"
DOCKERFILE_PATH = REPOSITORY_ROOT / "Dockerfile"


class NeverCalledProvider:
    async def evaluate(self, request: ProviderRequest) -> ProviderResult | ProviderFailure:
        raise AssertionError("provider must not be called")


def test_operational_metrics_use_only_bounded_labels_and_redact_sensitive_fields() -> None:
    telemetry = Telemetry()
    telemetry.record_provider_terminal(
        ProviderFailure(
            provider="customer-supplied-provider-name",
            kind=ProviderFailureKind.TIMEOUT,
            message="Synthetic secret must not become a metric label",
        ),
        duration=0.2,
    )
    telemetry.record_policy_outcome(PolicyOutcome.ACT)
    telemetry.record_regression_outcome(RegressionOutcome.PASS)
    payload, media_type = telemetry.prometheus_payload()
    rendered = payload.decode("utf-8")

    assert "text/plain" in media_type
    assert 'provider="other"' in rendered
    assert 'failure_kind="timeout"' in rendered
    assert 'outcome="act"' in rendered
    assert 'outcome="pass"' in rendered
    assert "customer-supplied-provider-name" not in rendered
    assert "Synthetic secret" not in rendered
    assert redact_fields(
        {
            "api_key": "not-for-logs",
            "state": {"message": "not-for-logs"},
            "provider": "typesafe_jev",
        }
    ) == {
        "api_key": "[REDACTED]",
        "state": "[REDACTED]",
        "provider": "typesafe_jev",
    }


def test_telemetry_emits_the_five_declared_span_names_without_payload_attributes() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Telemetry(tracer_provider=provider)

    with telemetry.decision_span():
        with telemetry.provider_span():
            pass
        with telemetry.policy_span():
            pass
    with telemetry.evaluation_aggregate_span():
        with telemetry.evaluation_case_span():
            pass

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "decision.execute",
        "provider.call",
        "policy.evaluate",
        "evaluation.aggregate",
        "evaluation.case",
    }
    assert all(not span.attributes for span in spans)


def test_dataset_limit_rejects_before_provider_execution() -> None:
    contract = _yaml_object(CONTRACT_PATH)
    dataset = _yaml_object(DATASET_PATH)
    service = DecisionOpsApplicationService(
        provider=NeverCalledProvider(),
        provider_execution_enabled=True,
        max_dataset_cases=1,
    )

    with pytest.raises(DatasetSizeLimitError, match="case limit"):
        asyncio.run(service.execute_evaluation(contract_data=contract, dataset_data=dataset))


def test_outbound_provider_allowlist_rejects_before_provider_execution() -> None:
    service = DecisionOpsApplicationService(
        provider=NeverCalledProvider(),
        provider_execution_enabled=True,
        provider_name="typesafe_jev",
        allowed_provider_names=frozenset({"system_one_shadow"}),
    )

    with pytest.raises(OutboundProviderNotAllowedError, match="allowlist"):
        asyncio.run(
            service.execute_decision(
                contract_data=_yaml_object(CONTRACT_PATH),
                state={"message": "Synthetic ticket"},
            )
        )


def test_container_runs_as_a_dedicated_non_root_user() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "useradd --create-home --uid 10001 decisionops" in dockerfile
    assert "USER decisionops" in dockerfile


def _yaml_object(path: Path) -> dict[str, object]:
    value = load_yaml(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
