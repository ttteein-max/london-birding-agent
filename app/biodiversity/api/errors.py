"""Unified safe API errors that never expose raw exceptions."""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.biodiversity.api.schemas import ErrorDetail, ErrorResponse


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        fields: list[str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.detail = ErrorDetail(
            code=code,
            message=message,
            fields=fields or [],
        )


def _response(status_code: int, detail: ErrorDetail) -> JSONResponse:
    body = ErrorResponse(error=detail)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
    return _response(exc.status_code, exc.detail)


async def validation_error_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    fields = sorted(
        {
            ".".join(str(part) for part in error.get("loc", ())[1:])
            for error in exc.errors()
            if error.get("loc")
        }
    )
    return _response(
        422,
        ErrorDetail(
            code="invalid_request",
            message="One or more request fields are invalid.",
            fields=[field for field in fields if field],
        ),
    )


async def internal_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return _response(
        500,
        ErrorDetail(
            code="internal_error",
            message="The request could not be completed safely.",
        ),
    )


def not_found(resource: str) -> APIError:
    return APIError(404, "not_found", f"The requested {resource} does not exist.")


def conflict(message: str) -> APIError:
    return APIError(409, "mutation_conflict", message)


def invalid_operation(message: str) -> APIError:
    return APIError(422, "invalid_operation", message)


def forbidden(message: str) -> APIError:
    return APIError(403, "mode_not_allowed", message)


def rate_limited() -> APIError:
    return APIError(
        429,
        "rate_limited",
        "The public demo mutation limit has been reached. Try again shortly.",
    )


def service_busy() -> APIError:
    return APIError(
        503,
        "service_busy",
        "The public demo is currently at capacity. Try again shortly.",
    )


def unavailable() -> APIError:
    return APIError(
        503,
        "service_unavailable",
        "The configured upstream runtime is unavailable.",
    )
