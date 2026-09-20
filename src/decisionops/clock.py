"""Testable time and identifier abstractions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4


class Clock(Protocol):
    """Supplies timezone-aware UTC time."""

    def now(self) -> datetime: ...


class IdentifierGenerator(Protocol):
    """Supplies UUIDs so tests do not depend on random identifiers."""

    def new(self) -> UUID: ...


class SystemClock:
    """Production clock implementation."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class Uuid4Generator:
    """Production identifier implementation."""

    def new(self) -> UUID:
        return uuid4()
