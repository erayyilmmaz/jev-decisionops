from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import typesafe_sdk as typesafe
from httpx2 import Headers
from pydantic import SecretStr

from decisionops.contracts import load_contract
from decisionops.models import ProviderFailure, ProviderFailureKind, ProviderResult
from decisionops.providers import JevDecisionProvider, JevProviderOptions, ProviderRequest


class FakeClient:
    def __init__(self, response_or_error: typesafe.SystemOneResponse | BaseException) -> None:
        self.response_or_error = response_or_error
        self.state: Any = None
        self.questions: Mapping[str, Any] | None = None

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        return None

    async def system_one(
        self,
        state: Any,
        questions: Mapping[str, Any],
    ) -> typesafe.SystemOneResponse:
        self.state = state
        self.questions = questions
        if isinstance(self.response_or_error, BaseException):
            raise self.response_or_error
        return self.response_or_error


def provider_request() -> ProviderRequest:
    validated = load_contract(Path("contracts/support-ticket-triage.yaml"))
    return ProviderRequest(
        contract=validated.contract,
        canonical_json=validated.canonical_json,
        fingerprint=validated.fingerprint,
        state={"message": "Synthetic test ticket", "account_status": "active"},
    )


def valid_response() -> typesafe.SystemOneResponse:
    return typesafe.SystemOneResponse(
        model="jev-resolved-2026-09-20",
        usage=typesafe.Usage(input_tokens=12, output_tokens=7),
        answers={
            "intent": typesafe.ChoiceAnswer(
                choice="refund",
                probabilities={
                    "refund": 0.96,
                    "cancellation": 0.01,
                    "support": 0.02,
                    "information": 0.01,
                },
                confidence=0.94,
            ),
            "unauthorized_activity": typesafe.NoulAnswer(noul=0.91),
            "urgency": typesafe.ScoreAnswer(
                score=2.7,
                legend={0: "low", 1: "medium", 2: "high", 3: "critical"},
                probabilities={0: 0.01, 1: 0.04, 2: 0.19, 3: 0.76},
                confidence=0.89,
            ),
        },
    )


@pytest.mark.anyio
async def test_provider_batches_contract_questions_and_maps_typed_answers() -> None:
    client = FakeClient(valid_response())
    captured: dict[str, object] = {}

    def factory(
        api_key: str,
        model: str,
        retry: typesafe.RetryPolicy,
        timeout: float,
    ) -> FakeClient:
        captured.update(api_key=api_key, model=model, retry=retry, timeout=timeout)
        return client

    clock_values = iter((10.0, 10.125))
    provider = JevDecisionProvider(
        api_key=SecretStr("test-key"),
        options=JevProviderOptions(model="jev-latest", timeout_seconds=10.0, max_retries=2),
        client_factory=factory,
        monotonic_clock=lambda: next(clock_values),
    )

    result = await provider.evaluate(provider_request())

    assert isinstance(result, ProviderResult)
    assert result.metadata.provider == "typesafe_jev"
    assert result.metadata.requested_model == "jev-latest"
    assert result.metadata.resolved_model == "jev-resolved-2026-09-20"
    assert result.metadata.request_id is None
    assert result.metadata.input_tokens == 12
    assert result.metadata.output_tokens == 7
    assert result.latency_ms == 125
    assert [answer.question_id for answer in result.answers] == [
        "intent",
        "unauthorized_activity",
        "urgency",
    ]
    assert client.questions is not None
    assert isinstance(client.questions["intent"], typesafe.Choice)
    assert isinstance(client.questions["unauthorized_activity"], typesafe.Noul)
    assert isinstance(client.questions["urgency"], typesafe.Score)
    assert captured["model"] == "jev-latest"
    assert captured["timeout"] == 10.0


@pytest.mark.anyio
async def test_missing_api_key_returns_configuration_failure_without_client_call() -> None:
    called = False

    def factory(
        api_key: str,
        model: str,
        retry: typesafe.RetryPolicy,
        timeout: float,
    ) -> FakeClient:
        nonlocal called
        called = True
        return FakeClient(valid_response())

    result = await JevDecisionProvider(api_key=None, client_factory=factory).evaluate(
        provider_request()
    )

    assert isinstance(result, ProviderFailure)
    assert result.kind == ProviderFailureKind.CONFIGURATION
    assert called is False


@pytest.mark.anyio
async def test_timeout_is_execution_failure_not_policy_outcome() -> None:
    def factory(
        api_key: str,
        model: str,
        retry: typesafe.RetryPolicy,
        timeout: float,
    ) -> FakeClient:
        return FakeClient(typesafe.TypeSafeAPITimeoutError(timeout=timeout))

    result = await JevDecisionProvider(
        api_key=SecretStr("test-key"),
        client_factory=factory,
    ).evaluate(provider_request())

    assert isinstance(result, ProviderFailure)
    assert result.kind == ProviderFailureKind.TIMEOUT


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (
            typesafe.TypeSafeAuthenticationError(status=401, body={}, headers=Headers()),
            ProviderFailureKind.AUTHENTICATION,
        ),
        (
            typesafe.TypeSafeRateLimitError(status=429, body={}, headers=Headers()),
            ProviderFailureKind.RATE_LIMITED,
        ),
    ],
)
async def test_api_errors_are_sanitized_and_mapped(
    error: BaseException,
    expected_kind: ProviderFailureKind,
) -> None:
    def factory(
        api_key: str,
        model: str,
        retry: typesafe.RetryPolicy,
        timeout: float,
    ) -> FakeClient:
        return FakeClient(error)

    result = await JevDecisionProvider(
        api_key=SecretStr("test-key"),
        client_factory=factory,
    ).evaluate(provider_request())

    assert isinstance(result, ProviderFailure)
    assert result.kind == expected_kind
    assert "test-key" not in result.message


@pytest.mark.anyio
async def test_missing_typed_answer_is_invalid_response_failure() -> None:
    incomplete = typesafe.SystemOneResponse(
        model="jev-resolved",
        usage=typesafe.Usage(input_tokens=1, output_tokens=1),
        answers={"unauthorized_activity": typesafe.NoulAnswer(noul=0.5)},
    )

    def factory(
        api_key: str,
        model: str,
        retry: typesafe.RetryPolicy,
        timeout: float,
    ) -> FakeClient:
        return FakeClient(incomplete)

    result = await JevDecisionProvider(
        api_key=SecretStr("test-key"),
        client_factory=factory,
    ).evaluate(provider_request())

    assert isinstance(result, ProviderFailure)
    assert result.kind == ProviderFailureKind.INVALID_RESPONSE
    assert "missing required answer" in result.message
