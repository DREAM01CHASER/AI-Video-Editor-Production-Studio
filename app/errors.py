"""Structured JSON errors for the local API.

Every failure returns the same envelope so the frontend can render a plain
message and still branch on a stable machine-readable code:

    {"error": {"code": "unreadable_media", "message": "..."}}
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .storage import UploadValidationError


class ApiError(Exception):
    """An error carrying a stable code, a human message, and an HTTP status."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_body(code: str, message: str) -> dict[str, object]:
    """Build the shared error envelope."""

    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    """Attach handlers that keep every error response in the shared shape."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=error_body(exc.code, exc.message)
        )

    @app.exception_handler(UploadValidationError)
    async def _upload_error(_: Request, exc: UploadValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content=error_body("invalid_upload", str(exc))
        )

    @app.exception_handler(RequestValidationError)
    async def _request_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "invalid_request", "The request is missing a required field or has an invalid value."
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=error_body("http_error", str(exc.detail))
        )
