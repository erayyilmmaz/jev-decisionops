from decisionops.config import Settings


def test_default_configuration_is_secretless_and_disables_live_calls() -> None:
    settings = Settings()

    assert settings.typesafe_api_key is None
    assert settings.typesafe_model == "jev-latest"
    assert settings.typesafe_timeout_seconds == 10.0
    assert settings.typesafe_max_retries == 2
    assert settings.live_provider_calls_enabled is False
    assert settings.environment == "development"
