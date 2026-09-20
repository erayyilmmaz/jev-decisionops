"""Actionable validation errors for labelled evaluation datasets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetIssue:
    """One safe, user-actionable issue in a dataset document."""

    path: str
    message: str
    code: str

    def render(self) -> str:
        """Render a stable compact error string for future CLI/API surfaces."""

        return f"{self.path}: {self.message} ({self.code})"


class DatasetValidationError(ValueError):
    """Raised when a dataset cannot be safely loaded or bound to a contract."""

    def __init__(self, issues: tuple[DatasetIssue, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.render() for issue in issues))


class MetricsEvaluationError(ValueError):
    """Raised when metric inputs cannot be compared reproducibly."""
