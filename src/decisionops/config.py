"""Configuration with secret values kept outside domain and persistence models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="DECISIONOPS_ENV"
    )
    database_url: str = Field(
        default=(
            "postgresql+asyncpg://decisionops:decisionops_dev_only@localhost:5432/decisionops"
        ),
        validation_alias="DATABASE_URL",
    )
    alembic_database_url: str = Field(
        default=(
            "postgresql+psycopg://decisionops:decisionops_dev_only@localhost:5432/decisionops"
        ),
        validation_alias="ALEMBIC_DATABASE_URL",
    )
    typesafe_api_key: SecretStr | None = Field(default=None, validation_alias="TYPESAFE_API_KEY")
    live_provider_calls_enabled: bool = Field(
        default=False, validation_alias="LIVE_PROVIDER_CALLS_ENABLED"
    )
