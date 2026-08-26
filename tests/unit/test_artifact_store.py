"""ArtifactStore writes files atomically and refuses path traversal (no network)."""

import hashlib

import pytest

from evoblue_video_mcp.storage.artifact_store import ArtifactIntegrityError, ArtifactStore


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


async def test_read_file_returns_content(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    content = b"hello"
    await store.write_file("job-1/x.json", content)
    assert await store.read_file("job-1/x.json") == content


async def test_read_file_verified(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    content = b"hello"
    content_hash, _ = await store.write_file("job-1/x.json", content)

    assert await store.read_file_verified("job-1/x.json", content_hash) == content
    with pytest.raises(ArtifactIntegrityError):
        await store.read_file_verified("job-1/x.json", "0" * 64)
