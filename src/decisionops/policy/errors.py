"""Policy evaluation errors that must not be converted into an outcome."""

from __future__ import annotations


class PolicyEvaluationError(ValueError):
    """Raised when a supposedly valid contract/result pair cannot be evaluated safely."""
