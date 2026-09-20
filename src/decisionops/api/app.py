"""Minimal FastAPI app factory; HTTP routes belong to JDO-12."""

from __future__ import annotations

from fastapi import FastAPI

from decisionops.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application without initializing a provider or database connection."""

    app = FastAPI(title="Jev DecisionOps", version="0.1.0")
    app.state.settings = settings or Settings()
    return app


app = create_app()
