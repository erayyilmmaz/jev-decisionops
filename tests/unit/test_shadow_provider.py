from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import system_one_adapter as adapter
import typesafe_sdk as typesafe
from pydantic import SecretStr

from decisionops.contracts import load_contract
from decisionops.models import ProviderFailure, ProviderFailureKind, ProviderResult
from decisionops.providers import LlmShadowProvider, ProviderRequest, ShadowProviderOptions


class FakeShadowClient:
    def __init__(self, response_or_error: adapter.SystemOneResponse | BaseException) -> None:
        self.response_or_error = response_or_error
        self.state: Any = None
        self.questions: Mapping[str, Any] | None = None
        self.closed = False

    async def system_one(
        self,
        state: Any,
        questions: Mapping[str, Any],
    ) -> adapter.SystemOneResponse:
        self.state = state
        self.questions = questions
        if isinstance(self.response_or_error, BaseException):
            raise self.response_or_error
        return self.response_or_error

    async def aclose(self) -> None:
        self.closed = True


def provider_request() -> ProviderRequest:
    validated = load_contract(Path("contracts/support-ticket-triage.yaml"))
    return ProviderRequest(
        contract=validated.contract,
        canonical_json=validated.canonical_json,
        fingerprint=validated.fingerprint,
        state={"message": "Synthetic shadow test ticket", "account_status": "active"},
    )


def adapter_response() -> adapter.SystemOneResponse:
    return adapter.SystemOneResponse(
        model="shadow-resolved-model",
        usage=adapter.Usage(
            input_tokens=12,
            output_tokens=7,
            input_tokens_total=19,
            output_tokens_total=11,
            n_retries=1,
            n_retries_malformed_structure=1,
            latency=0.125,
        ),
        answers={
            "intent": adapter.ChoiceAnswer(
                choice="refund",
                probabilities={
                    "refund": 0.96,
                    "cancellation": 0.01,
                    "support": 0.02,
                    "information": 0.01,
                },
                confidence=0.94,
            ),
            "unauthorized_activity": adapter.NoulAnswer(noul=0.91),
            "urgency": adapter.ScoreAnswer(
                score=2.7,
                legend={0: "low", 1: "medium", 2: "high", 3: "critical"},
                probabilities={0: 0.01, 1: 0.04, 2: 0.19, 3: 0.76},
                confidence=0.89,
            ),
        },
        debug={
            "llm_attempts": [
                {
                    "messages": [{"role": "user", "content": "must never be persisted"}],
                    "llm_response": {"secret": "must never be persisted"},
                }
            ]
        },
    )


@pytest.mark.anyio
async def test_shadow_uses_adapter_response_and_keeps_debug_history_isolated() -> None:
    client = FakeShadowClient(adapter_response())
    captured: dict[str, object] = {}

    def factory(
        api_key: SecretStr,
        options: ShadowProviderOptions,
        retry: typesafe.RetryPolicy,
    ) -> FakeShadowClient:
        captured.update(api_key=api_key, options=options, retry=retry)
        return client

    options = ShadowProviderOptions(
        model="shadow-requested-model",
        base_url="https://shadow.example/v1",
        timeout_seconds=20.0,
        max_retries=1,
        malformed_retries=1,
    )
    provider = LlmShadowProvider(
        enabled=True,
        api_key=SecretStr("shadow-test-key"),
        options=options,
        client_factory=factory,
    )

    result = await provider.evaluate(provider_request())

    assert isinstance(result, ProviderResult)
    assert result.metadata.provider == "openai_compatible_shadow"
    assert result.metadata.requested_model == "shadow-requested-model"
    assert result.metadata.resolved_model == "shadow-resolved-model"
    assert result.metadata.sdk_version == adapter.__version__
    assert result.metadata.input_tokens == 19
    assert result.metadata.output_tokens == 11
    assert result.latency_ms == 125
    assert client.closed is True
    assert client.questions is not None
    assert isinstance(client.questions["intent"], typesafe.Choice)
    assert isinstance(client.questions["unauthorized_activity"], typesafe.Noul)
    assert isinstance(client.questions["urgency"], typesafe.Score)
    assert captured["options"] == options
    assert "Synthetic shadow test ticket" not in result.model_dump_json()
    assert "must never be persisted" not in result.model_dump_json()


@pytest.mark.anyio
async def test_shadow_is_disabled_by_default_without_constructing_client() -> None:
    called = False

    def factory(
        api_key: SecretStr,
        options: ShadowProviderOptions,
        retry: typesafe.RetryPolicy,
    ) -> FakeShadowClient:
        nonlocal called
        called = True
        return FakeShadowClient(adapter_response())

    result = await LlmShadowProvider(
        enabled=False,
        api_key=SecretStr("shadow-test-key"),
        client_factory=factory,
    ).evaluate(provider_request())

    assert isinstance(result, ProviderFailure)
    assert result.kind == ProviderFailureKind.CONFIGURATION
    assert result.provider == "openai_compatible_shadow"
    assert called is False


@pytest.mark.anyio
async def test_shadow_requires_an_api_key_when_enabled() -> None:
    result = await LlmShadowProvider(enabled=True, api_key=None).evaluate(provider_request())

    assert isinstance(result, ProviderFailure)
    assert result.kind == ProviderFailureKind.CONFIGURATION
    assert result.provider == "openai_compatible_shadow"


@pytest.mark.anyio
async def test_shadow_timeout_is_a_provider_failure_and_client_is_closed() -> None:
    client = FakeShadowClient(typesafe.TypeSafeAPITimeoutError(timeout=20.0))

    def factory(
        api_key: SecretStr,
        options: ShadowProviderOptions,
        retry: typesafe.RetryPolicy,
    ) -> FakeShadowClient:
        return client

    result = await LlmShadowProvider(
        enabled=True,
        api_key=SecretStr("shadow-test-key"),
        client_factory=factory,
    ).evaluate(provider_request())

    assert isinstance(result, ProviderFailure)
    assert result.kind == ProviderFailureKind.TIMEOUT
    assert result.provider == "openai_compatible_shadow"
    assert client.closed is True


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ShadowProviderOptions(model=" "),
        lambda: ShadowProviderOptions(timeout_seconds=60.1),
        lambda: ShadowProviderOptions(max_retries=4),
    ],
)
def test_shadow_options_are_bounded(factory: Callable[[], ShadowProviderOptions]) -> None:
    with pytest.raises(ValueError):
        factory()
