"""Frozen stable error codes for the MCP tool surface.

Authority is the error table in ``docs/MCP_TOOLS.md`` §0.2; the contract drift
test keeps this tuple and the table in lockstep. Order here matches the table.
This module also owns the Bridge→client error translation matrix: Engine HTTP
failures (legacy ``{"detail": ...}`` and P3 ``{"error": {code, message}}``
shapes, transport errors) map here — and only here — to frozen codes (ADR 0004).
"""

from typing import Literal

import httpx

from evoblue_video_mcp.mcp.schemas import ToolError, ToolFailure

ErrorCode = Literal[
    "INVALID_URL",
    "UNSUPPORTED_PLATFORM",
    "ENGINE_NOT_READY",
    "ENGINE_TIMEOUT",
    "ENGINE_UNAUTHORIZED",
    "ENGINE_PROTOCOL_ERROR",
    "JOB_NOT_FOUND",
    "REPORT_NOT_READY",
    "SECTION_NOT_AVAILABLE",
    "REPORT_READ_FAILED",
    "INVALID_FILTER",
    "QUERY_INVALID",
    "SEARCH_INDEX_UNAVAILABLE",
    "QUEUE_FULL",
    "BRIDGE_INTERNAL",
]

STABLE_ERROR_CODES: tuple[ErrorCode, ...] = (
    "INVALID_URL",
    "UNSUPPORTED_PLATFORM",
    "ENGINE_NOT_READY",
    "ENGINE_TIMEOUT",
    "ENGINE_UNAUTHORIZED",
    "ENGINE_PROTOCOL_ERROR",
    "JOB_NOT_FOUND",
    "REPORT_NOT_READY",
    "SECTION_NOT_AVAILABLE",
    "REPORT_READ_FAILED",
    "INVALID_FILTER",
    "QUERY_INVALID",
    "SEARCH_INDEX_UNAVAILABLE",
    "QUEUE_FULL",
    "BRIDGE_INTERNAL",
)

#: Frozen retryable semantics (§0.2 table column two).
_RETRYABLE_CODES: frozenset[str] = frozenset(
    {"ENGINE_NOT_READY", "ENGINE_TIMEOUT", "REPORT_NOT_READY", "SEARCH_INDEX_UNAVAILABLE"}
)

#: Engine error code → MCP tool code. Codes the Engine emits that no MCP tool
#: can surface (e.g. REBUILD_ALREADY_RUNNING) fall through to
#: ENGINE_PROTOCOL_ERROR instead of inventing unmapped codes.
_ENGINE_CODE_TO_MCP: dict[str, str] = {
    "INVALID_URL": "INVALID_URL",
    "UNSUPPORTED_PLATFORM": "UNSUPPORTED_PLATFORM",
    "JOB_NOT_FOUND": "JOB_NOT_FOUND",
    "HISTORY_ITEM_NOT_FOUND": "JOB_NOT_FOUND",
    "REPORT_FILE_MISSING": "REPORT_NOT_READY",
    "SECTION_NOT_AVAILABLE": "SECTION_NOT_AVAILABLE",
    "REPORT_READ_FAILED": "REPORT_READ_FAILED",
    "INVALID_FILTER": "INVALID_FILTER",
    "QUERY_INVALID": "QUERY_INVALID",
    "SEARCH_INDEX_UNAVAILABLE": "SEARCH_INDEX_UNAVAILABLE",
}


def is_retryable(code: str) -> bool:
    """Return the frozen §0.2 retryable semantics for a code."""
    return code in _RETRYABLE_CODES


def tool_failure(
    code: ErrorCode,
    message: str,
    *,
    retryable: bool = False,
    detail: str | None = None,
) -> ToolFailure:
    """Build a frozen failure payload (``ok: false`` + error envelope)."""
    return ToolFailure(
        ok=False,
        error=ToolError(code=code, message=message, retryable=retryable, detail=detail),
    )


def tool_failure_from(error: ToolError) -> ToolFailure:
    """Wrap a translated :class:`ToolError` into a tool failure payload."""
    return ToolFailure(ok=False, error=error)


def translate_request_error(exc: httpx.RequestError) -> ToolError:
    """Map a transport failure to ENGINE_NOT_READY / ENGINE_TIMEOUT.

    ``detail`` carries only the exception type name — never the message, which
    can embed local URLs or stack context.
    """
    detail = type(exc).__name__
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return ToolError(
            code="ENGINE_NOT_READY",
            message="Local Engine 未启动或端口不可达, 请先启动 EvoBlue Local Engine",
            retryable=True,
            detail=detail,
        )
    if isinstance(exc, httpx.TimeoutException):
        return ToolError(
            code="ENGINE_TIMEOUT",
            message="Local Engine 未在预算时间内响应, 可稍后重试",
            retryable=True,
            detail=detail,
        )
    return ToolError(
        code="ENGINE_TIMEOUT",
        message="与 Local Engine 的连接中断, 可稍后重试",
        retryable=True,
        detail=detail,
    )


#: Legacy ``{"detail": "<CODE>"}`` bodies carry no human message; the Bridge
#: supplies one. Today only the submit endpoint emits these (422 PlatformError).
_LEGACY_MESSAGES: dict[str, str] = {
    "INVALID_URL": "提交的 URL 未通过校验",
    "UNSUPPORTED_PLATFORM": "平台不受支持",
}


def _error_code_and_message(body: object) -> tuple[str, str] | None:
    """Extract ``(code, message)`` from the P3 ``{"error": {...}}`` envelope."""
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    message = error.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        return None
    return code, message


def _legacy_detail(body: object) -> str | None:
    """Extract the legacy ``{"detail": "..."}`` string, if present."""
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
    return None


def translate_status(status_code: int, body: object | None) -> ToolError | None:
    """Map a non-2xx Engine response onto the frozen error envelope.

    Returns ``None`` for 2xx (success path). Any response shape this matrix
    does not recognize becomes ``ENGINE_PROTOCOL_ERROR`` (retryable only for
    5xx) with ``detail="HTTP <status>"`` — never a raw response body.
    """
    if 200 <= status_code < 300:
        return None
    if status_code == 401:
        return ToolError(
            code="ENGINE_UNAUTHORIZED",
            message="本机 token 校验失败, 请在 WebUI 重新完成本机配对",
            retryable=False,
            detail=None,
        )
    structured = _error_code_and_message(body)
    if structured is not None:
        engine_code, message = structured
        mapped = _ENGINE_CODE_TO_MCP.get(engine_code)
        if mapped is not None:
            return ToolError(
                code=mapped,
                message=message,
                retryable=is_retryable(mapped),
                detail=None,
            )
    legacy = _legacy_detail(body)
    if legacy is not None:
        if legacy == "job not found":
            return ToolError(
                code="JOB_NOT_FOUND",
                message="任务不存在",
                retryable=False,
                detail=None,
            )
        mapped = _ENGINE_CODE_TO_MCP.get(legacy)
        if mapped is not None:
            message = _LEGACY_MESSAGES.get(mapped, "请求被 Local Engine 拒绝")
            return ToolError(
                code=mapped,
                message=message,
                retryable=is_retryable(mapped),
                detail=None,
            )
    return ToolError(
        code="ENGINE_PROTOCOL_ERROR",
        message="Local Engine 返回了未预期的响应",
        retryable=status_code >= 500,
        detail=f"HTTP {status_code}",
    )
