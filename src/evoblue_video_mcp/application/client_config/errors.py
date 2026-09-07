"""Stable HTTP error codes and exception types for the client-config API.

Authority: docs/CLIENT_CONFIG_WRITE_CONTRACT.md §7 error table; the drift test
keeps the table and :data:`CLIENT_API_ERROR_CODES` in lockstep, in table order.
These are HTTP-API codes for the WebUI surface — deliberately separate from
the MCP tool envelope's 15 stable codes (``mcp/errors.py``).
"""

from typing import Literal

ClientConfigErrorCode = Literal[
    "CLIENT_UNKNOWN",
    "BACKUP_NOT_FOUND",
    "ENTRY_MODIFIED",
    "OPERATION_IN_PROGRESS",
    "AUTO_INSTALL_UNSUPPORTED",
    "CONFIRMATION_REQUIRED",
    "INVALID_REQUEST",
    "CONFIG_WRITE_FAILED",
    "CONFIG_UNSUPPORTED",
]

CLIENT_API_ERROR_CODES: tuple[ClientConfigErrorCode, ...] = (
    "CLIENT_UNKNOWN",
    "BACKUP_NOT_FOUND",
    "ENTRY_MODIFIED",
    "OPERATION_IN_PROGRESS",
    "AUTO_INSTALL_UNSUPPORTED",
    "CONFIRMATION_REQUIRED",
    "INVALID_REQUEST",
    "CONFIG_WRITE_FAILED",
    "CONFIG_UNSUPPORTED",
)


class ClientConfigError(Exception):
    """Base class carrying the stable code + HTTP status for API translation."""

    status_code: int = 500
    code: str = "CONFIG_WRITE_FAILED"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ClientUnknownError(ClientConfigError):
    status_code = 404
    code = "CLIENT_UNKNOWN"


class BackupNotFoundError(ClientConfigError):
    status_code = 404
    code = "BACKUP_NOT_FOUND"


class EntryConflictError(ClientConfigError):
    """The existing entry carries unmanaged keys (contract §4.3)."""

    status_code = 409
    code = "ENTRY_MODIFIED"

    def __init__(self, message: str, extra_keys: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.extra_keys = extra_keys


class OperationInProgressError(ClientConfigError):
    status_code = 409
    code = "OPERATION_IN_PROGRESS"


class AutoInstallUnsupportedError(ClientConfigError):
    status_code = 409
    code = "AUTO_INSTALL_UNSUPPORTED"


class ConfirmationRequiredError(ClientConfigError):
    status_code = 422
    code = "CONFIRMATION_REQUIRED"


class InvalidRequestError(ClientConfigError):
    status_code = 422
    code = "INVALID_REQUEST"


class ConfigWriteError(ClientConfigError):
    """Target unwritable, or post-commit proof failed (rollback reported)."""

    status_code = 503
    code = "CONFIG_WRITE_FAILED"


class UnsupportedConfigError(ClientConfigError):
    """Target exists but its shape is unsupported — fail closed (§4.1/§4.2)."""

    status_code = 503
    code = "CONFIG_UNSUPPORTED"
