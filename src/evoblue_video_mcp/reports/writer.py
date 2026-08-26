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


@dataclass(frozen=True)
class ReportWriteResult:
    path: str
    content_hash: str
    conflict: bool


def safe_filename(title: str, analysis_id: str) -> str:
    """Derive a Windows-safe filename from the title, falling back to analysis id."""
    cleaned = _INVALID_FILENAME_CHARS.sub("", title).strip().rstrip(". ")
    base = cleaned or f"analysis-{analysis_id}"
    return f"{base}.md"


class ReportWriter:
    """Write Markdown reports atomically, refusing to clobber user edits."""

    def __init__(self, report_dir: str | Path) -> None:
        self._root = Path(report_dir).resolve()

    async def write_report(
        self, filename: str, content: str, previous_hash: str | None = None
    ) -> ReportWriteResult:
        """Write ``content``; if the file changed since ``previous_hash``, write a copy."""
        return await asyncio.to_thread(self._write_sync, filename, content, previous_hash)

    def _write_sync(
        self, filename: str, content: str, previous_hash: str | None
    ) -> ReportWriteResult:
        final = self._resolve(filename)
        final.parent.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        conflict = False
        if previous_hash is not None and final.exists():
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
