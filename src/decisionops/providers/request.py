"""Provider input that carries a fully validated, versioned Decision Contract."""

from __future__ import annotations

from pydantic import ConfigDict

from decisionops.contracts.schema import ValidatedContract
from decisionops.models import JsonValue


class ProviderRequest(ValidatedContract):
    """Validated contract plus state; no SDK-specific request shape is exposed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: dict[str, JsonValue]
    correlation_id: str | None = None
