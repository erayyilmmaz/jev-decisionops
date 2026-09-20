"""Small deterministic factories shared by unit and future integration tests."""

from __future__ import annotations

from decisionops.models import DecisionContractReference, DecisionRequest


def contract_reference() -> DecisionContractReference:
    """Return a stable contract identity without loading a YAML contract."""

    return DecisionContractReference(
        name="support-ticket-triage",
        version=1,
        fingerprint="a" * 64,
    )


def decision_request() -> DecisionRequest:
    """Return a synthetic request that contains no personal or provider data."""

    return DecisionRequest(
        contract=contract_reference(),
        state={"message": "Synthetic support ticket", "account_status": "active"},
    )
