from decisionops.api.app import create_app
from decisionops.config import Settings


def test_app_factory_has_no_provider_or_database_startup_side_effect() -> None:
    app = create_app(Settings())

    assert app.title == "Jev DecisionOps"
    assert app.state.settings.live_provider_calls_enabled is False
