"""ArtifactStore writes files atomically and refuses path traversal (no network)."""

import hashlib

import pytest

from evoblue_video_mcp.storage.artifact_store import ArtifactStore


async def test_write_file_atomically(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    content = b'{"segments": []}'

    content_hash, byte_size = await store.write_file("job-1/transcript.json", content)

    assert byte_size == len(content)
    assert content_hash == hashlib.sha256(content).hexdigest()
    assert (tmp_path / "job-1" / "transcript.json").read_bytes() == content
    # No temporary file is left behind.
    assert not (tmp_path / "job-1" / "transcript.json.tmp").exists()


async def test_write_file_rejects_path_escape(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(ValueError):
        await store.write_file("../escape.txt", b"x")
