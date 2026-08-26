"""Atomic Markdown report writing with user-edit conflict protection."""

import asyncio
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from evoblue_video_mcp.storage.artifact_store import ArtifactIntegrityError

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)
_MAX_COMPONENT = 200


class ReportConflictError(RuntimeError):
    """Raised when writing would clobber a file with no known generated hash."""


@dataclass(frozen=True)
class ReportWriteResult:
    path: str
    content_hash: str
    conflict: bool


def safe_filename(title: str, analysis_id: str) -> str:
    """Derive a Windows-safe filename, escaping reserved names and length limits."""
    cleaned = _INVALID_FILENAME_CHARS.sub("", title).strip().rstrip(". ")
    if not cleaned:
        cleaned = f"analysis-{analysis_id}"
    if cleaned.lower() in _WINDOWS_RESERVED:
        cleaned = f"{cleaned}-{analysis_id}"
    if len(cleaned) > _MAX_COMPONENT:
        cleaned = cleaned[:_MAX_COMPONENT].rstrip(". ")
    return f"{cleaned}.md"


def report_filename(title: str, analysis_id: str, content_hash: str) -> str:
    """Content-addressed filename so different content never collides."""
    base = safe_filename(title, analysis_id)
    return f"{base[:-3]}-{content_hash[:8]}.md"


class ReportWriter:
    """Write Markdown reports atomically, refusing to clobber unknown files."""

    def __init__(self, report_dir: str | Path) -> None:
        self._root = Path(report_dir).resolve()

    async def write_report(
        self, filename: str, content: str, previous_hash: str | None = None
    ) -> ReportWriteResult:
        """Write ``content``; refuse to overwrite a file whose hash we do not know."""
        return await asyncio.to_thread(self._write_sync, filename, content, previous_hash)

    def _write_sync(
        self, filename: str, content: str, previous_hash: str | None
    ) -> ReportWriteResult:
        final = self._resolve(filename)
        final.parent.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        conflict = False
        if final.exists():
            if previous_hash is None:
                raise ReportConflictError(
                    f"report already exists and has no known generated hash: {filename}"
                )
            existing_hash = hashlib.sha256(final.read_bytes()).hexdigest()
            if existing_hash != previous_hash:
                conflict = True
                final = final.with_name(
                    f"{final.stem}.conflict-{uuid.uuid4().hex[:8]}{final.suffix}"
                )

        tmp = final.with_name(f"{final.name}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, final)

        return ReportWriteResult(
            path=final.relative_to(self._root).as_posix(),
            content_hash=content_hash,
            conflict=conflict,
        )

    async def read_file_verified(self, filename: str, expected_hash: str) -> str:
        """Read a report file and verify its content hash."""
        final = self._resolve(filename)
        content = final.read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_hash:
            raise ArtifactIntegrityError(
                f"report hash mismatch: expected {expected_hash}, got {actual}"
            )
        return content.decode("utf-8")

    def _resolve(self, filename: str) -> Path:
        candidate = (self._root / filename).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError(f"report path escapes root: {filename!r}")
        return candidate
