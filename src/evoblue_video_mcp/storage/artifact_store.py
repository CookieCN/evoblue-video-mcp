"""Atomic file-based storage for large stage artifacts."""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path


class ArtifactIntegrityError(RuntimeError):
    """Raised when a file artifact's content hash does not match its registry."""


class ArtifactStore:
    """Write artifact files atomically under a root directory.

    Files are written to a temporary path, fsynced, then atomically renamed to
    their final relative path. Relative paths are validated to stay inside the
    root to prevent path traversal.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    async def write_file(self, relative_path: str, content: bytes) -> tuple[str, int]:
        """Atomically write ``content``; return ``(sha256_hex, byte_size)``."""
        return await asyncio.to_thread(self._write_file_sync, relative_path, content)

    def _write_file_sync(self, relative_path: str, content: bytes) -> tuple[str, int]:
        final = self._resolve(relative_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.with_name(f"{final.name}.{uuid.uuid4().hex}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, final)
        content_hash = hashlib.sha256(content).hexdigest()
        return content_hash, len(content)

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self._root / relative_path).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError(f"artifact path escapes root: {relative_path!r}")
        return candidate

    async def read_file(self, relative_path: str) -> bytes:
        """Read an artifact file, validating the path stays under the root."""
        return await asyncio.to_thread(self._read_file_sync, relative_path)

    def _read_file_sync(self, relative_path: str) -> bytes:
        return self._resolve(relative_path).read_bytes()

    async def read_file_verified(self, relative_path: str, expected_hash: str) -> bytes:
        """Read an artifact file and verify its content hash."""
        content = await self.read_file(relative_path)
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_hash:
            raise ArtifactIntegrityError(
                f"artifact hash mismatch: expected {expected_hash}, got {actual}"
            )
        return content
