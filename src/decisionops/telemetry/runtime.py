"""Fail-open OpenTelemetry spans, Prometheus metrics, and safe structured logging."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from time import monotonic
from typing import Any

from opentelemetry import context, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, Tracer
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from decisionops.models import PolicyOutcome, ProviderFailure, ProviderResult, RegressionOutcome

_ALLOWED_PROVIDER_LABELS = frozenset(
    {"typesafe_jev", "system_one_shadow", "replay_runtime", "application_runtime"}
)
_SENSITIVE_FIELD_TOKENS = frozenset(
    {
        "api_key",
        "authorization",
        "body",
        "message",
        "password",
        "response",
        "secret",
        "state",
        "token",
    }
)


class Telemetry:
    """Records bounded operational telemetry without payloads or availability coupling."""

    def __init__(
        self,
        *,
        service_name: str = "jev-decisionops",
        registry: CollectorRegistry | None = None,
        tracer_provider: TracerProvider | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._registry = registry or CollectorRegistry()
        self._tracer: Tracer = (
            tracer_provider.get_tracer(service_name)
            if tracer_provider is not None
            else trace.get_tracer(service_name)
        )
        self._logger = logger or _structured_logger()
        self._provider_requests = Counter(
            "decisionops_provider_requests_total",
            "Provider execution attempts by normalized provider.",
            ("provider",),
            registry=self._registry,
        )
        self._provider_errors = Counter(
            "decisionops_provider_errors_total",
            "Provider execution failures by normalized provider and kind.",
            ("provider", "failure_kind"),
            registry=self._registry,
        )
        self._provider_latency = Histogram(
            "decisionops_provider_latency_seconds",
            "Provider call latency in seconds.",
            ("provider",),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
            registry=self._registry,
        )
        self._policy_outcomes = Counter(
            "decisionops_policy_outcomes_total",
            "Deterministic policy dispositions.",
            ("outcome",),
            registry=self._registry,
        )
        self._evaluation_duration = Histogram(
            "decisionops_evaluation_duration_seconds",
            "Full labelled evaluation duration in seconds.",
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
            registry=self._registry,
        )
        self._regression_results = Counter(
            "decisionops_regression_comparisons_total",
            "Regression comparison results.",
            ("outcome",),
            registry=self._registry,
        )

    @contextmanager
    def decision_span(self) -> Iterator[None]:
        """Create the root decision-execution span without request attributes."""

        with self._span("decision.execute"):
            yield

    @contextmanager
    def provider_span(self) -> Iterator[None]:
        """Create a provider-call span without state, secret, or response attributes."""

        with self._span("provider.call"):
            yield

    @contextmanager
    def policy_span(self) -> Iterator[None]:
        """Create a deterministic policy-evaluation span without answer payloads."""

        with self._span("policy.evaluate"):
            yield

    @contextmanager
    def evaluation_case_span(self) -> Iterator[None]:
        """Create one replay-case span without case ID, state, or labels as attributes."""

        with self._span("evaluation.case"):
            yield

    @contextmanager
    def evaluation_aggregate_span(self) -> Iterator[None]:
        """Create an aggregate replay span and record its duration safely."""

        started = monotonic()
        try:
            with self._span("evaluation.aggregate"):
                yield
        finally:
            self._safe_metric(lambda: self._evaluation_duration.observe(monotonic() - started))

    def record_provider_terminal(
        self, terminal: ProviderResult | ProviderFailure, duration: float
    ) -> None:
        """Record one provider terminal state using only allow-listed label values."""

        provider = _provider_label(
            terminal.metadata.provider
            if isinstance(terminal, ProviderResult)
            else terminal.provider
        )
        self._safe_metric(lambda: self._provider_requests.labels(provider=provider).inc())
        self._safe_metric(
            lambda: self._provider_latency.labels(provider=provider).observe(duration)
        )
        if isinstance(terminal, ProviderFailure):
            self._safe_metric(
                lambda: self._provider_errors.labels(
                    provider=provider,
                    failure_kind=terminal.kind.value,
                ).inc()
            )
            self.log_event(
                "provider.failure",
                provider=provider,
                failure_kind=terminal.kind.value,
            )
        else:
            self.log_event("provider.success", provider=provider)

    def record_policy_outcome(self, outcome: PolicyOutcome) -> None:
        """Record a fixed policy disposition with no policy trace content."""

        self._safe_metric(lambda: self._policy_outcomes.labels(outcome=outcome.value).inc())
        self.log_event("policy.evaluated", outcome=outcome.value)

    def record_regression_outcome(self, outcome: RegressionOutcome) -> None:
        """Record one fixed regression result without artifact identity labels."""

        self._safe_metric(lambda: self._regression_results.labels(outcome=outcome.value).inc())
        self.log_event("regression.compared", outcome=outcome.value)

    def prometheus_payload(self) -> tuple[bytes, str]:
        """Render this service's dedicated Prometheus registry without mutable global state."""

        try:
            return generate_latest(self._registry), CONTENT_TYPE_LATEST
        except Exception:
            return b"", CONTENT_TYPE_LATEST

    def log_event(self, event: str, **fields: object) -> None:
        """Emit a JSON log event after defensive field-name and value redaction."""

        try:
            payload = {"event": event, **redact_fields(fields)}
            self._logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        except Exception:
            return

    @contextmanager
    def _span(self, name: str) -> Iterator[None]:
        """Keep trace-provider failures from changing business execution semantics."""

        span: Span | None = None
        token: object | None = None
        try:
            span = self._tracer.start_span(name)
            token = context.attach(trace.set_span_in_context(span))
        except Exception:
            yield
            return
        try:
            yield
        except BaseException as error:
            try:
                span.record_exception(error)
            except Exception:
                pass
            raise
        finally:
            if token is not None:
                try:
                    context.detach(token)
                except Exception:
                    pass
            try:
                span.end()
            except Exception:
                pass

    @staticmethod
    def _safe_metric(callback: Any) -> None:
        try:
            callback()
        except Exception:
            return


def redact_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Redact sensitive fields recursively before a structured event can leave the process."""

    return {name: _redacted_value(name, value) for name, value in fields.items()}


def _redacted_value(name: str, value: object) -> object:
    normalized_name = name.lower()
    if any(token in normalized_name for token in _SENSITIVE_FIELD_TOKENS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return redact_fields({str(key): item for key, item in value.items()})
    if isinstance(value, list | tuple):
        return [_redacted_value(name, item) for item in value]
    return value


def _provider_label(provider: str | None) -> str:
    if provider in _ALLOWED_PROVIDER_LABELS:
        return provider
    return "other"


def _structured_logger() -> logging.Logger:
    """Configure one isolated logger whose message body is the sanitized JSON event."""

    logger = logging.getLogger("decisionops.telemetry")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
