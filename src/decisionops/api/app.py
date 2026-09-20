"""FastAPI delivery surface backed only by the shared application service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.base import RequestResponseEndpoint

from decisionops.application import (
    DatasetSizeLimitError,
    DecisionOpsApplicationService,
    DecisionPolicyExecutionError,
    EvaluationNotFoundError,
    OutboundProviderNotAllowedError,
    ProviderExecutionDisabledError,
)
from decisionops.config import Settings
from decisionops.contracts import ContractValidationError
from decisionops.evaluation import DatasetValidationError
from decisionops.models import (
    DecisionExecutionArtifact,
    EvaluationMetricsReport,
    JsonValue,
    ReplayRunArtifact,
)

type ServiceFactory = Callable[[Settings], DecisionOpsApplicationService]
type ReadinessCheck = Callable[[], Awaitable[bool]]


class ApiRequestModel(BaseModel):
    """Strict transport model; contract and dataset validation stay in core services."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionRequestBody(ApiRequestModel):
    contract: dict[str, object]
    state: dict[str, JsonValue]


class EvaluationRequestBody(ApiRequestModel):
    contract: dict[str, object]
    dataset: dict[str, object]


def create_app(
    settings: Settings | None = None,
    *,
    service_factory: ServiceFactory | None = None,
    readiness_check: ReadinessCheck | None = None,
) -> FastAPI:
    """Create a side-effect-free API app with request limits and mapped safe errors."""

    resolved_settings = settings or Settings()
    service = (service_factory or DecisionOpsApplicationService.from_settings)(resolved_settings)
    app = FastAPI(title="Jev DecisionOps", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.service = service

    @app.middleware("http")
    async def attach_correlation_and_enforce_size(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        correlation_id = _correlation_id(request.headers.get("X-Correlation-ID"))
        request.state.correlation_id = correlation_id
        declared_length = request.headers.get("content-length")
        response: Response
        if declared_length is not None and _content_length_exceeds(
            declared_length, resolved_settings.api_max_request_bytes
        ):
            response = _error_response(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                code="request_too_large",
                message="request body exceeds the configured size limit",
            )
        else:
            body = await request.body()
            if len(body) > resolved_settings.api_max_request_bytes:
                response = _error_response(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    code="request_too_large",
                    message="request body exceeds the configured size limit",
                )
            else:
                response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    @app.exception_handler(ContractValidationError)
    async def contract_validation_error(_: Request, error: ContractValidationError) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_contract",
            message="Decision Contract validation failed",
            details=[
                {"path": issue.path, "message": issue.message, "code": issue.code}
                for issue in error.issues
            ],
        )

    @app.exception_handler(DatasetValidationError)
    async def dataset_validation_error(_: Request, error: DatasetValidationError) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_dataset",
            message="evaluation dataset validation failed",
            details=[
                {"path": issue.path, "message": issue.message, "code": issue.code}
                for issue in error.issues
            ],
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="invalid_request",
            message="request schema validation failed",
            details=error.errors(),
        )

    @app.exception_handler(ProviderExecutionDisabledError)
    async def provider_execution_disabled(
        _: Request, __: ProviderExecutionDisabledError
    ) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="provider_execution_disabled",
            message="live provider execution is disabled",
        )

    @app.exception_handler(OutboundProviderNotAllowedError)
    async def outbound_provider_not_allowed(
        _: Request, __: OutboundProviderNotAllowedError
    ) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="outbound_provider_not_allowed",
            message="configured provider is not available for outbound execution",
        )

    @app.exception_handler(DatasetSizeLimitError)
    async def dataset_size_limit(_: Request, __: DatasetSizeLimitError) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            code="dataset_too_large",
            message="evaluation dataset exceeds the configured case limit",
        )

    @app.exception_handler(DecisionPolicyExecutionError)
    async def policy_execution_error(_: Request, __: DecisionPolicyExecutionError) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="policy_execution_failed",
            message="deterministic policy evaluation failed",
        )

    @app.exception_handler(EvaluationNotFoundError)
    async def evaluation_not_found(_: Request, __: EvaluationNotFoundError) -> JSONResponse:
        return _error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="evaluation_not_found",
            message="evaluation artifact was not found",
        )

    @app.post("/v1/decisions", response_model=DecisionExecutionArtifact)
    async def create_decision(
        payload: DecisionRequestBody, response: Response
    ) -> DecisionExecutionArtifact:
        artifact = await service.execute_decision(
            contract_data=payload.contract,
            state=payload.state,
        )
        response.headers["X-Decision-Run-ID"] = str(artifact.run_id)
        if artifact.failure is not None:
            response.status_code = status.HTTP_502_BAD_GATEWAY
        return artifact

    @app.post(
        "/v1/evaluations",
        response_model=ReplayRunArtifact,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_evaluation(
        payload: EvaluationRequestBody,
        response: Response,
    ) -> ReplayRunArtifact:
        artifact = await service.execute_evaluation(
            contract_data=payload.contract,
            dataset_data=payload.dataset,
        )
        response.headers["X-Evaluation-ID"] = str(artifact.evaluation_id)
        return artifact

    @app.get("/v1/evaluations/{evaluation_id}", response_model=ReplayRunArtifact)
    async def get_evaluation(evaluation_id: UUID) -> ReplayRunArtifact:
        return service.get_evaluation(evaluation_id)

    @app.get("/v1/evaluations/{evaluation_id}/report", response_model=EvaluationMetricsReport)
    async def get_evaluation_report(evaluation_id: UUID) -> EvaluationMetricsReport:
        return service.get_evaluation(evaluation_id).metrics

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        dependency_ready = await readiness_check() if readiness_check is not None else True
        if not dependency_ready:
            return _error_response(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="dependencies_not_ready",
                message="one or more required local dependencies are not ready",
            )
        return JSONResponse({"status": "ready", "checks": service.readiness()})

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        payload, media_type = service.prometheus_metrics()
        return Response(content=payload, media_type=media_type)

    return app


def _correlation_id(value: str | None) -> str:
    if value is not None:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return str(uuid4())


def _content_length_exceeds(value: str, limit: int) -> bool:
    try:
        return int(value) > limit
    except ValueError:
        return True


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: object | None = None,
) -> JSONResponse:
    content: dict[str, object] = {"error": {"code": code, "message": message}}
    if details is not None:
        error = content["error"]
        assert isinstance(error, dict)
        error["details"] = details
    return JSONResponse(status_code=status_code, content=content)


app = create_app()
