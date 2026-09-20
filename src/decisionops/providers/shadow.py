"""Isolated OpenAI-compatible LLM shadow provider via the official adapter."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

import system_one_adapter as adapter
import typesafe_sdk as typesafe
from pydantic import SecretStr
from system_one_adapter.providers.openai import AsyncOpenAIProvider

from decisionops.config import Settings
from decisionops.models import ProviderFailure, ProviderFailureKind, ProviderResult
from decisionops.providers.request import ProviderRequest
from decisionops.providers.system_one import (
    build_system_one_questions,
    map_system_one_response,
    safe_response_latency_ms,
    safe_usage_total,
)


class AsyncShadowClient(Protocol):
    """Small adapter surface that keeps unit tests secretless and local."""

    async def system_one(
        self,
        state: Any,
        questions: Mapping[str, Any],
    ) -> typesafe.SystemOneResponse: ...

    async def aclose(self) -> None: ...


type ShadowClientFactory = Callable[
    [SecretStr, ShadowProviderOptions, typesafe.RetryPolicy], AsyncShadowClient
]


@dataclass(frozen=True)
class ShadowProviderOptions:
    """Bounded and explicit V0 OpenAI-compatible shadow configuration."""

    model: str = "gpt-4o-mini"
    base_url: str | None = None
    timeout_seconds: float = 20.0
    max_retries: int = 1
    malformed_retries: int = 1
    api: Literal["responses", "chat_completions"] | None = None

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be non-empty")
        if self.base_url is not None and not self.base_url.strip():
            raise ValueError("base_url must be non-empty when configured")
        if not 0.0 < self.timeout_seconds <= 60.0:
            raise ValueError("timeout_seconds must be within (0, 60]")
        if not 0 <= self.max_retries <= 3:
            raise ValueError("max_retries must be within [0, 3]")
        if not 0 <= self.malformed_retries <= 3:
            raise ValueError("malformed_retries must be within [0, 3]")


class LlmShadowProvider:
    """Run the same typed contract through an isolated OpenAI-compatible shadow."""

    def __init__(
        self,
        *,
        enabled: bool,
        api_key: SecretStr | None,
        options: ShadowProviderOptions | None = None,
        client_factory: ShadowClientFactory | None = None,
    ) -> None:
        self._enabled = enabled
        self._api_key = api_key
        self._options = options or ShadowProviderOptions()
        self._client_factory = client_factory or _default_client_factory
        logging.getLogger("system_one_adapter").setLevel(logging.WARNING)

    async def evaluate(self, request: ProviderRequest) -> ProviderResult | ProviderFailure:
        """Evaluate once without reading or exporting adapter debug/attempt history."""

        if not self._enabled:
            return ProviderFailure(
                provider="openai_compatible_shadow",
                kind=ProviderFailureKind.CONFIGURATION,
                message="LLM shadow provider is disabled",
            )
        if self._api_key is None:
            return ProviderFailure(
                provider="openai_compatible_shadow",
                kind=ProviderFailureKind.CONFIGURATION,
                message="LLM shadow API key is not configured",
            )

        retry_policy = typesafe.RetryPolicy(
            max_retries=self._options.max_retries,
            backoff_initial=0.1,
            backoff_max=0.5,
            backoff_jitter=0.0,
            timeout=self._options.timeout_seconds,
        )
        client = self._client_factory(self._api_key, self._options, retry_policy)
        try:
            try:
                response = await client.system_one(
                    state=request.state,
                    questions=build_system_one_questions(request),
                )
            finally:
                await client.aclose()
        except typesafe.TypeSafeAuthenticationError as error:
            return _failure(
                ProviderFailureKind.AUTHENTICATION,
                "LLM shadow authentication failed",
                error,
            )
        except typesafe.TypeSafeRateLimitError as error:
            return _failure(
                ProviderFailureKind.RATE_LIMITED, "LLM shadow rate limit reached", error
            )
        except typesafe.TypeSafeAPITimeoutError as error:
            return _failure(ProviderFailureKind.TIMEOUT, "LLM shadow request timed out", error)
        except typesafe.TypeSafeAPIConnectionError as error:
            return _failure(ProviderFailureKind.TRANSPORT, "LLM shadow connection failed", error)
        except typesafe.TypeSafeAPIResponseValidationError as error:
            return _failure(
                ProviderFailureKind.INVALID_RESPONSE,
                "LLM shadow response validation failed",
                error,
            )
        except typesafe.TypeSafeError as error:
            return _failure(ProviderFailureKind.UNKNOWN, "LLM shadow request failed", error)
        except Exception:
            return ProviderFailure(
                provider="openai_compatible_shadow",
                kind=ProviderFailureKind.UNKNOWN,
                message="unexpected LLM shadow execution failure",
            )

        return map_system_one_response(
            request,
            response,
            provider="openai_compatible_shadow",
            requested_model=self._options.model,
            sdk_version=adapter.__version__,
            latency_ms=safe_response_latency_ms(response),
            input_tokens=safe_usage_total(response, "input_tokens_total"),
            output_tokens=safe_usage_total(response, "output_tokens_total"),
        )


def shadow_provider_from_settings(settings: Settings) -> LlmShadowProvider:
    """Build the disabled-by-default shadow from explicit environment configuration."""

    base_url = settings.shadow_openai_base_url
    return LlmShadowProvider(
        enabled=settings.shadow_provider_enabled,
        api_key=settings.shadow_openai_api_key,
        options=ShadowProviderOptions(
            model=settings.shadow_openai_model,
            base_url=base_url.strip() if base_url is not None else None,
            timeout_seconds=settings.shadow_timeout_seconds,
            max_retries=settings.shadow_max_retries,
            malformed_retries=settings.shadow_malformed_retries,
        ),
    )


def _default_client_factory(
    api_key: SecretStr,
    options: ShadowProviderOptions,
    retry_policy: typesafe.RetryPolicy,
) -> AsyncShadowClient:
    provider = AsyncOpenAIProvider(
        options.model,
        base_url=options.base_url,
        api_key=api_key.get_secret_value(),
        api=options.api,
    )
    return cast(
        AsyncShadowClient,
        adapter.AsyncSystemOneAdapterClient(
            structured_outputs=True,
            llm_answer_mode="probabilities",
            normalize_probabilities=False,
            n_retry_malformed_structure=options.malformed_retries,
            retry=retry_policy,
            model=provider,
        ),
    )


def _failure(
    kind: ProviderFailureKind,
    message: str,
    error: BaseException,
) -> ProviderFailure:
    request_id = getattr(error, "request_id", None)
    return ProviderFailure(
        provider="openai_compatible_shadow",
        kind=kind,
        message=message,
        request_id=request_id if isinstance(request_id, str) else None,
    )
