"""Standardized error codes, exceptions, and response envelopes."""

from __future__ import annotations

from enum import Enum
from typing import Any
from fastapi import HTTPException

from radar.tracing import get_trace_id


class ErrorCode(str, Enum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    GATEWAY_ERROR = "GATEWAY_ERROR"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    BAD_REQUEST = "BAD_REQUEST"


_STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.LLM_RATE_LIMIT,
    500: ErrorCode.INTERNAL_SERVER_ERROR,
    502: ErrorCode.GATEWAY_ERROR,
    503: ErrorCode.GATEWAY_ERROR,
    504: ErrorCode.LLM_TIMEOUT,
}


class AppException(HTTPException):
    """Application-level structured HTTP exception."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: ErrorCode | None = None,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code or _STATUS_TO_ERROR_CODE.get(
            status_code, ErrorCode.INTERNAL_SERVER_ERROR
        )
        self.retryable = retryable


def format_error_envelope(
    detail: Any,
    status_code: int = 500,
    *,
    error_code: ErrorCode | str | None = None,
    retryable: bool | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Format a consistent error envelope for client consumption."""
    tid = trace_id or get_trace_id()
    if error_code is None:
        code_enum = _STATUS_TO_ERROR_CODE.get(status_code, ErrorCode.INTERNAL_SERVER_ERROR)
        code_str = code_enum.value
    elif isinstance(error_code, ErrorCode):
        code_str = error_code.value
    else:
        code_str = str(error_code)

    if retryable is None:
        retryable = status_code in {408, 429, 502, 503, 504}

    return {
        "detail": detail,
        "error_code": code_str,
        "retryable": retryable,
        "trace_id": tid,
    }
