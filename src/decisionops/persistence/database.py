"""SQLAlchemy foundation with no tables or migration side effects yet."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for JDO-7 persistence entities."""


def create_database_engine(database_url: str) -> AsyncEngine:
    """Build an async engine without opening a database connection."""

    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build an async session factory for future application services."""

    return async_sessionmaker(engine, expire_on_commit=False)
