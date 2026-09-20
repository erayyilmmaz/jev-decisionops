"""Actionable contract-validation errors without provider side effects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractIssue:
    """One user-actionable problem at a contract field or document location."""

    path: str
    message: str
    code: str

    def render(self) -> str:
        """Render a stable compact error string for humans and future CLI output."""

        return f"{self.path}: {self.message} ({self.code})"


class ContractValidationError(ValueError):
    """Raised when a contract cannot be safely loaded or validated."""

    def __init__(self, issues: tuple[ContractIssue, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.render() for issue in issues))
