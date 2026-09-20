from datetime import UTC
from uuid import UUID

from decisionops.clock import SystemClock, Uuid4Generator


def test_system_clock_returns_utc_time() -> None:
    assert SystemClock().now().tzinfo is UTC


def test_uuid_generator_returns_uuid() -> None:
    assert isinstance(Uuid4Generator().new(), UUID)
