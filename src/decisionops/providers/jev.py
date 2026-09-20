"""Official TypeSafe Jev adapter behind the provider abstraction boundary."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from types import TracebackType
from typing import Any, Protocol, cast

import typesafe_sdk as typesafe
from pydantic import SecretStr, ValidationError

from decisionops.config import Settings
from decisionops.contracts.schema import ChoiceQuestion, NoulQuestion, ScoreQuestion
from decisionops.models import (
    ChoiceAnswer,
    DecisionAnswer,
    NoulAnswer,
    ProviderFailure,
    ProviderFailureKind,
    ProviderMetadata,
    ProviderResult,
    ScoreAnswer,
)
from decisionops.providers.request import ProviderRequest


class AsyncSystemOneClient(Protocol):
    """Small testable surface used from the official async client."""

    async def __aenter__(self) -> AsyncSystemOneClient: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    async def system_one(
        self,
        state: Any,
        questions: Mapping[str, Any],
    ) -> typesafe.SystemOneResponse: ...


type ClientFactory = Callable[[str, str, typesafe.RetryPolicy, float], AsyncSystemOneClient]
type MonotonicClock = Callable[[], float]


@dataclass(frozen=True)
class JevProviderOptions:
    """Bounded provider configuration recorded in every successful result."""

    model: str = "jev-latest"
    timeout_seconds: float = 10.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be non-empty")
        if not 0.0 < self.timeout_seconds <= 60.0:
            raise ValueError("timeout_seconds must be within (0, 60]")
        if not 0 <= self.max_retries <= 3:
            raise ValueError("max_retries must be within [0, 3]")


class JevDecisionProvider:
    """Maps validated contracts to the official SDK and back to common domain results."""

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        options: JevProviderOptions | None = None,
        client_factory: ClientFactory | None = None,
        monotonic_clock: MonotonicClock = monotonic,
    ) -> None:
        self._api_key = api_key
        self._options = options or JevProviderOptions()
        self._client_factory = client_factory or _default_client_factory
        self._monotonic_clock = monotonic_clock
        logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)

    async def evaluate(self, request: ProviderRequest) -> ProviderResult | ProviderFailure:
        """Call Jev once with all contract questions, or return a sanitized failure."""

        if self._api_key is None:
            return ProviderFailure(
                kind=ProviderFailureKind.CONFIGURATION,
                message="TypeSafe API key is not configured",
            )

        questions = _build_questions(request)
        retry_policy = typesafe.RetryPolicy(
            max_retries=self._options.max_retries,
            backoff_initial=0.1,
            backoff_max=0.5,
            backoff_jitter=0.0,
            timeout=self._options.timeout_seconds,
        )
        started = self._monotonic_clock()

        try:
            async with self._client_factory(
                self._api_key.get_secret_value(),
                self._options.model,
                retry_policy,
                self._options.timeout_seconds,
            ) as client:
                response = await client.system_one(state=request.state, questions=questions)
        except typesafe.TypeSafeAuthenticationError as error:
            return _failure(
                ProviderFailureKind.AUTHENTICATION, "TypeSafe authentication failed", error
            )
        except typesafe.TypeSafeRateLimitError as error:
            return _failure(ProviderFailureKind.RATE_LIMITED, "TypeSafe rate limit reached", error)
        except typesafe.TypeSafeAPITimeoutError as error:
            return _failure(ProviderFailureKind.TIMEOUT, "TypeSafe request timed out", error)
        except typesafe.TypeSafeAPIConnectionError as error:
            return _failure(ProviderFailureKind.TRANSPORT, "TypeSafe connection failed", error)
        except typesafe.TypeSafeAPIResponseValidationError as error:
            return _failure(
                ProviderFailureKind.INVALID_RESPONSE,
                "TypeSafe response validation failed",
                error,
            )
        except typesafe.TypeSafeError as error:
            return _failure(ProviderFailureKind.UNKNOWN, "TypeSafe request failed", error)
        except Exception:
            return ProviderFailure(
                kind=ProviderFailureKind.UNKNOWN,
                message="unexpected provider execution failure",
            )

        latency_ms = max(0, round((self._monotonic_clock() - started) * 1000))
        return _map_response(request, response, latency_ms, self._options.model)


def jev_provider_from_settings(settings: Settings) -> JevDecisionProvider:
    """Create the official provider with bounded configuration from application settings."""

    return JevDecisionProvider(
        api_key=settings.typesafe_api_key,
        options=JevProviderOptions(
            model=settings.typesafe_model,
            timeout_seconds=settings.typesafe_timeout_seconds,
            max_retries=settings.typesafe_max_retries,
        ),
    )


def _default_client_factory(
    api_key: str,
    model: str,
    retry_policy: typesafe.RetryPolicy,
    timeout_seconds: float,
) -> AsyncSystemOneClient:
    return cast(
        AsyncSystemOneClient,
        typesafe.AsyncTypeSafeClient(
            api_key=api_key,
            model=model,
            retry=retry_policy,
            timeout=timeout_seconds,
        ),
    )


def _build_questions(
    request: ProviderRequest,
) -> dict[str, typesafe.Noul | typesafe.Choice | typesafe.Score]:
    questions: dict[str, typesafe.Noul | typesafe.Choice | typesafe.Score] = {}
    for question_id, question in request.contract.questions.items():
        if isinstance(question, NoulQuestion):
            questions[question_id] = typesafe.Noul(
                instructions=question.instructions,
                criteria=cast(Any, question.criteria),
            )
        elif isinstance(question, ChoiceQuestion):
            questions[question_id] = typesafe.Choice(
                instructions=question.instructions,
                criteria=question.criteria,
            )
        elif isinstance(question, ScoreQuestion):
            questions[question_id] = typesafe.Score(
                instructions=question.instructions,
                criteria=question.criteria,
            )
    return questions


def _map_response(
    request: ProviderRequest,
    response: typesafe.SystemOneResponse,
    latency_ms: int,
    requested_model: str,
) -> ProviderResult | ProviderFailure:
    answers: list[DecisionAnswer] = []
    for question_id in request.contract.questions:
        answer = response.answers.get(question_id)
        if answer is None:
            return ProviderFailure(
                kind=ProviderFailureKind.INVALID_RESPONSE,
                message=f"TypeSafe response is missing required answer {question_id!r}",
            )
        if isinstance(answer, typesafe.NoulAnswer):
            answers.append(NoulAnswer(question_id=question_id, noul=answer.noul))
        elif isinstance(answer, typesafe.ChoiceAnswer):
            answers.append(
                ChoiceAnswer(
                    question_id=question_id,
                    choice=answer.choice,
                    probabilities=answer.probabilities,
                    confidence=answer.confidence,
                )
            )
        elif isinstance(answer, typesafe.ScoreAnswer):
            answers.append(
                ScoreAnswer(
                    question_id=question_id,
                    score=answer.score,
                    legend=cast(dict[int, Any], answer.legend),
                    probabilities=answer.probabilities,
                    confidence=answer.confidence,
                )
            )
        else:
            return ProviderFailure(
                kind=ProviderFailureKind.INVALID_RESPONSE,
                message=f"TypeSafe returned unsupported answer for {question_id!r}",
            )

    try:
        return ProviderResult(
            metadata=ProviderMetadata(
                provider="typesafe_jev",
                requested_model=requested_model,
                resolved_model=response.model,
                sdk_version=typesafe.__version__,
                request_id=_response_request_id(response),
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            answers=tuple(answers),
            latency_ms=latency_ms,
        )
    except ValidationError:
        return ProviderFailure(
            kind=ProviderFailureKind.INVALID_RESPONSE,
            message="TypeSafe returned an invalid typed answer",
        )


def _response_request_id(response: typesafe.SystemOneResponse) -> str | None:
    try:
        return response.request_id
    except typesafe.TypeSafeError:
        return None


def _failure(
    kind: ProviderFailureKind,
    message: str,
    error: BaseException,
) -> ProviderFailure:
    request_id = getattr(error, "request_id", None)
    return ProviderFailure(
        kind=kind,
        message=message,
        request_id=request_id if isinstance(request_id, str) else None,
    )
