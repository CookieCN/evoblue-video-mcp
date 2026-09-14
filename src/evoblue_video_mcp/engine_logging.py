"""Minimal rolling file log for the Engine (feedback fix F0).

Contract: docs/ENGINE_LOGGING.md — location, rotation caps, redaction rules,
and degrade semantics. The Engine entry (``__main__.py``) is the only caller;
the STDIO bridge never activates file logging (stdout carries MCP protocol
only). Importing this module has no side effects.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_DIRNAME = "logs"
LOG_FILENAME = "engine.log"
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_BACKUP_COUNT = 3

# Best-effort credential shapes for the file sink's bottom-line net (contract
# section 5): new log statements still follow the field allowlist themselves.
_AUTH_HEADER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)[^\r\n]*")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SK_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
_REDACTED = "[REDACTED]"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_KNOWN_SECRET_MIN_LENGTH = 16


def scrub_sensitive_text(text: str, known_secrets: Sequence[re.Pattern[str]]) -> str:
    """Redact credential shapes and the exact known secrets from one log line."""
    redacted = _BEARER_RE.sub(_REDACTED, text)
    redacted = _SK_KEY_RE.sub(_REDACTED, redacted)
    redacted = _AUTH_HEADER_RE.sub(r"\1" + _REDACTED, redacted)
    for pattern in known_secrets:
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


class _RedactingFormatter(logging.Formatter):
    """Formatter that scrubs the full rendered output, tracebacks included."""

    def __init__(self, known_secrets: Sequence[str]) -> None:
        super().__init__(_LOG_FORMAT)
        self._secret_patterns = tuple(
            re.compile(re.escape(secret))
            for secret in known_secrets
            if len(secret) >= _KNOWN_SECRET_MIN_LENGTH
        )

    def format(self, record: logging.LogRecord) -> str:
        return scrub_sensitive_text(super().format(record), self._secret_patterns)


@dataclass(frozen=True)
class EngineFileLog:
    """An attached rolling file handler plus where it writes."""

    path: Path
    handler: RotatingFileHandler


def setup_engine_file_logging(
    data_dir: Path,
    *,
    known_secrets: Sequence[str] = (),
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> EngineFileLog | None:
    """Attach the engine file log; ``None`` means degraded (never fatal).

    Any ``OSError`` while creating the logs directory or opening the file
    disables file logging entirely — the Engine keeps starting and stderr
    keeps working (contract section 6). Callers must only invoke this after
    the single-instance guard: a second instance must not write the first
    instance's log file.
    """
    logs_dir = data_dir / LOG_DIRNAME
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            logs_dir / LOG_FILENAME,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError:
        return None
    handler.setFormatter(_RedactingFormatter(known_secrets))
    root = logging.getLogger()
    root.addHandler(handler)
    # App-level records previously reached the console only through the
    # lastResort fallback (WARNING+ bare messages); a configured root drops
    # that path, so stderr keeps a real handler — same information or more.
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(stderr)
    root.setLevel(logging.INFO)
    return EngineFileLog(path=logs_dir / LOG_FILENAME, handler=handler)


def _existing_handler_factory(
    handler: logging.Handler,
) -> Callable[[], logging.Handler]:
    # dictConfig instantiates handlers from its config; returning the already
    # attached instance keeps exactly one RotatingFileHandler on the file —
    # two instances of the same process holding one file would break Windows
    # rotation renames.
    def factory() -> logging.Handler:
        return handler

    return factory


def engine_log_config(log: EngineFileLog) -> dict[str, Any]:
    """Build the uvicorn ``log_config`` dict for a configured file log.

    Extends uvicorn's stock config (stderr formatters untouched) so the file
    handler also receives uvicorn's own records: uvicorn's dictConfig
    replaces the ``uvicorn``/``uvicorn.access`` handler lists, while the root
    logger (not named in that config) keeps the handlers attached by setup.
    """
    from copy import deepcopy

    from uvicorn.config import LOGGING_CONFIG

    config = deepcopy(LOGGING_CONFIG)
    config["handlers"]["engine_file"] = {"()": _existing_handler_factory(log.handler)}
    for name, stock in (("uvicorn", "default"), ("uvicorn.access", "access")):
        config["loggers"][name]["handlers"] = [stock, "engine_file"]
    return config
