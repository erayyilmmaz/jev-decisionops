FROM ghcr.io/astral-sh/uv:0.11.17 AS uv

FROM python:3.14.4-slim

COPY --from=uv /uv /uvx /bin/

RUN useradd --create-home --uid 10001 decisionops
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

USER decisionops
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "decisionops.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
