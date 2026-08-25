"""Atomic file-based storage for large stage artifacts."""

import asyncio
import hashlib
import os
from pathlib import Path


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
        tmp = final.with_name(final.name + ".tmp")
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
