"""Provider protocol; SDK-specific integrations must remain behind this boundary."""

from __future__ import annotations

from typing import Protocol

from decisionops.models import DecisionRequest, ProviderFailure, ProviderResult


class DecisionProvider(Protocol):
    """Evaluates one request and returns either a valid result or a failure object."""

    async def evaluate(self, request: DecisionRequest) -> ProviderResult | ProviderFailure: ...
