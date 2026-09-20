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
    typesafe_model: str = Field(default="jev-latest", validation_alias="TYPESAFE_MODEL")
    typesafe_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=60.0,
        validation_alias="TYPESAFE_TIMEOUT_SECONDS",
    )
    typesafe_max_retries: int = Field(
        default=2,
        ge=0,
        le=3,
        validation_alias="TYPESAFE_MAX_RETRIES",
    )
    live_provider_calls_enabled: bool = Field(
        default=False, validation_alias="LIVE_PROVIDER_CALLS_ENABLED"
    )
    shadow_provider_enabled: bool = Field(default=False, validation_alias="SHADOW_PROVIDER_ENABLED")
    shadow_openai_api_key: SecretStr | None = Field(
        default=None, validation_alias="SHADOW_OPENAI_API_KEY"
    )
    shadow_openai_model: str = Field(default="gpt-4o-mini", validation_alias="SHADOW_OPENAI_MODEL")
    shadow_openai_base_url: str | None = Field(
        default=None, validation_alias="SHADOW_OPENAI_BASE_URL"
    )
    shadow_timeout_seconds: float = Field(
        default=20.0,
        gt=0.0,
        le=60.0,
        validation_alias="SHADOW_TIMEOUT_SECONDS",
    )
    shadow_max_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        validation_alias="SHADOW_MAX_RETRIES",
    )
    shadow_malformed_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        validation_alias="SHADOW_MALFORMED_RETRIES",
    )
    api_max_request_bytes: int = Field(
        default=65_536,
        ge=1_024,
        le=1_048_576,
        validation_alias="API_MAX_REQUEST_BYTES",
    )
