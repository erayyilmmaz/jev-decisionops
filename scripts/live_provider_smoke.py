"""Manually dispatched live TypeSafe smoke test with synthetic input only."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from decisionops.config import Settings
from decisionops.contracts import load_contract
from decisionops.models import ProviderFailure
from decisionops.providers import ProviderRequest, jev_provider_from_settings


async def run() -> int:
    settings = Settings()
    if not settings.live_provider_calls_enabled:
        print(json.dumps({"error": "LIVE_PROVIDER_CALLS_ENABLED must be true"}))
        return 2
    contract = load_contract(Path("contracts/support-ticket-triage.yaml"))
    result = await jev_provider_from_settings(settings).evaluate(
        ProviderRequest(
            contract=contract.contract,
            canonical_json=contract.canonical_json,
            fingerprint=contract.fingerprint,
            state={
                "message": "Synthetic smoke test: duplicate charge.",
                "account_status": "active",
            },
        )
    )
    if isinstance(result, ProviderFailure):
        print(json.dumps({"provider": result.provider, "failure_kind": result.kind.value}))
        return 3
    print(
        json.dumps(
            {
                "provider": result.metadata.provider,
                "requested_model": result.metadata.requested_model,
                "resolved_model": result.metadata.resolved_model,
                "latency_ms": result.latency_ms,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
