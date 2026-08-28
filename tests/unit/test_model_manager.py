"""Model manager download and install primitives."""

import hashlib
import io
import stat
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from evoblue_video_mcp.asr.manifest import ModelManifest
from evoblue_video_mcp.asr.model_manager import (
    ArchiveError,
    ContentRangeError,
    ResponseTooLargeError,
    download_resumable,
    install_archive,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _client(
    content: bytes, status_code: int = 200, headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=content, headers=headers)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _manifest(files: list[dict], archive_format: str = "tar.bz2") -> ModelManifest:
    return ModelManifest.model_validate(
        {
            "model_id": "m",
            "version": "v",
            "provider": "p",
            "languages": ["zh"],
            "platforms": ["windows-x86_64"],
            "compressed_size_bytes": 100,
            "installed_size_bytes": sum(f["size_bytes"] for f in files),
            "license": "l",
            "attribution": "a",
            "upstream_url": "https://github.com/x/y",
            "redistribution": "blocked",
            "archive_format": archive_format,
            "sources": [
                {
                    "url": "https://github.com/x/y.tar.bz2",
                    "kind": "upstream",
                    "sha256": "a" * 64,
                    "size_bytes": 100,
                }
            ],
            "files": files,
        }
    )


def _write_tar(
    path: Path, entries: dict[str, bytes], symlinks: dict[str, str] | None = None
) -> None:
    with tarfile.open(path, "w:bz2") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)


def _write_zip(
    path: Path, entries: dict[str, bytes], symlinks: dict[str, str] | None = None
) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)


# --- download_resumable -----------------------------------------------------


async def test_download_resumable_writes_and_verifies(tmp_path) -> None:
    content = b"hello world"
    dest = tmp_path / "model.tar.bz2"
    await download_resumable(
        "https://example.com/m.tar.bz2", dest, _sha(content), len(content), _client(content)
    )
    assert dest.read_bytes() == content


async def test_download_resumable_rejects_bad_sha(tmp_path) -> None:
    content = b"hello world"
    dest = tmp_path / "model.tar.bz2"
    with pytest.raises(ValueError):
        await download_resumable(
            "https://example.com/m", dest, "0" * 64, len(content), _client(content)
        )


async def test_download_resumable_rejects_bad_size(tmp_path) -> None:
    content = b"hello world"
    dest = tmp_path / "model.tar.bz2"
    with pytest.raises(ValueError):
        await download_resumable(
            "https://example.com/m", dest, _sha(content), len(content) + 1, _client(content)
        )


async def test_download_resumable_resumes_partial(tmp_path) -> None:
    full = b"0123456789"
    dest = tmp_path / "m.bin"
    dest.write_bytes(b"01234")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Range") == "bytes=5-"
        return httpx.Response(206, content=b"56789", headers={"Content-Range": "bytes 5-9/10"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    outcome = await download_resumable(
        "https://example.com/m", dest, _sha(full), len(full), client
    )
    assert dest.read_bytes() == full
    assert outcome.downloaded_bytes == len(full)
    assert outcome.restarted is False


async def test_download_resumable_rejects_missing_content_range(tmp_path) -> None:
    dest = tmp_path / "m.bin"
    dest.write_bytes(b"01234")
    client = _client(b"56789", status_code=206)
    with pytest.raises(ContentRangeError):
        await download_resumable(
            "https://example.com/m", dest, _sha(b"0123456789"), 10, client
        )


async def test_download_resumable_rejects_wrong_content_range_start(tmp_path) -> None:
    dest = tmp_path / "m.bin"
    dest.write_bytes(b"01234")
    client = _client(b"56789", status_code=206, headers={"Content-Range": "bytes 0-9/10"})
    with pytest.raises(ContentRangeError):
        await download_resumable(
            "https://example.com/m", dest, _sha(b"0123456789"), 10, client
        )


async def test_download_resumable_rejects_wrong_content_range_total(tmp_path) -> None:
    dest = tmp_path / "m.bin"
    dest.write_bytes(b"01234")
    client = _client(b"56789", status_code=206, headers={"Content-Range": "bytes 5-9/99"})
    with pytest.raises(ContentRangeError):
        await download_resumable(
            "https://example.com/m", dest, _sha(b"0123456789"), 10, client
        )


async def test_download_resumable_stops_on_oversized_response(tmp_path) -> None:
    dest = tmp_path / "m.bin"
    with pytest.raises(ResponseTooLargeError):
        await download_resumable(
            "https://example.com/m", dest, _sha(b"0123456789"), 5, _client(b"0123456789")
        )
    assert dest.stat().st_size <= 5


async def test_download_resumable_skips_request_when_complete(tmp_path) -> None:
    content = b"complete"
    dest = tmp_path / "m.bin"
    dest.write_bytes(content)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be issued when the file is complete")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    outcome = await download_resumable(
        "https://example.com/m", dest, _sha(content), len(content), client
    )
    assert outcome.downloaded_bytes == len(content)
    assert dest.read_bytes() == content


async def test_download_resumable_ignores_range_and_restarts(tmp_path) -> None:
    full = b"0123456789"
    dest = tmp_path / "m.bin"
    dest.write_bytes(b"01234")
    restarted: list[bool] = []

    async def on_restart() -> None:
        restarted.append(True)

    client = _client(full, status_code=200)
    outcome = await download_resumable(
        "https://example.com/m",
        dest,
        _sha(full),
        len(full),
        client,
        on_restart=on_restart,
    )
    assert dest.read_bytes() == full
    assert outcome.restarted is True
    assert restarted == [True]


async def test_download_resumable_sends_if_range_with_etag(tmp_path) -> None:
    full = b"0123456789"
    dest = tmp_path / "m.bin"
    dest.write_bytes(b"01234")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("If-Range") == '"abc123"'
        return httpx.Response(206, content=b"56789", headers={"Content-Range": "bytes 5-9/10"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await download_resumable(
        "https://example.com/m", dest, _sha(full), len(full), client, etag='"abc123"'
    )


async def test_download_resumable_returns_validators(tmp_path) -> None:
    content = b"0123456789"
    dest = tmp_path / "m.bin"
    client = _client(
        content,
        status_code=200,
        headers={"ETag": '"etag-1"', "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
    )
    outcome = await download_resumable(
        "https://example.com/m", dest, _sha(content), len(content), client
    )
    assert outcome.etag == '"etag-1"'
    assert outcome.last_modified == "Wed, 01 Jan 2025 00:00:00 GMT"


# --- install_archive --------------------------------------------------------


def test_install_archive_rejects_undeclared_file(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"model.int8.onnx": model, "extra.txt": b"undeclared"})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_promotes_atomically(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    dest = tmp_path / "installed"
    install_archive(archive, manifest, dest)
    assert (dest / "model.int8.onnx").read_bytes() == model
    assert not list(tmp_path.glob(".installed.staging-*"))


def test_install_archive_rejects_size_mismatch(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model) + 1, "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_rejects_oversized_member(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model) - 1, "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_raw_format(tmp_path) -> None:
    vad = b"vad-model-bytes"
    raw = tmp_path / "silero_vad.onnx"
    raw.write_bytes(vad)
    files = [{"name": "silero_vad.onnx", "size_bytes": len(vad), "sha256": _sha(vad)}]
    manifest = _manifest(files, archive_format="raw")
    dest = tmp_path / "installed"
    install_archive(raw, manifest, dest)
    assert (dest / "silero_vad.onnx").read_bytes() == vad


def test_install_archive_rejects_parent_traversal(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"../model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_rejects_absolute_path(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"/model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_rejects_nested_path(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"a/b/model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_allows_single_root(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"single-root/model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    dest = tmp_path / "installed"
    install_archive(archive, manifest, dest)
    assert (dest / "model.int8.onnx").read_bytes() == model


def test_install_archive_rejects_symlink(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(
        archive, {"model.int8.onnx": model}, symlinks={"evil-link": "model.int8.onnx"}
    )
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_rejects_duplicate_member(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"a/model.int8.onnx": model, "b/model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_rejects_multiple_roots(tmp_path) -> None:
    weights = b"model-weights"
    tokens = b"token-vocab"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"root-a/model.int8.onnx": weights, "root-b/tokens.txt": tokens})
    files = [
        {"name": "model.int8.onnx", "size_bytes": len(weights), "sha256": _sha(weights)},
        {"name": "tokens.txt", "size_bytes": len(tokens), "sha256": _sha(tokens)},
    ]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_rejects_empty_segment(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"root//model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_rejects_dot_segment(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"root/./model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_zip_rejects_symlink(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.zip"
    _write_zip(archive, {"model.int8.onnx": model}, symlinks={"evil-link": "model.int8.onnx"})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files, archive_format="zip")
    with pytest.raises(ArchiveError):
        install_archive(archive, manifest, tmp_path / "installed")


def test_install_archive_reuses_existing_identical_dir(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    dest = tmp_path / "installed"
    install_archive(archive, manifest, dest)
    install_archive(archive, manifest, dest)
    assert (dest / "model.int8.onnx").read_bytes() == model


def test_install_archive_rejects_existing_different_dir(tmp_path) -> None:
    model = b"model-weights"
    archive = tmp_path / "m.tar.bz2"
    _write_tar(archive, {"model.int8.onnx": model})
    files = [{"name": "model.int8.onnx", "size_bytes": len(model), "sha256": _sha(model)}]
    manifest = _manifest(files)
    dest = tmp_path / "installed"
    dest.mkdir(parents=True)
    (dest / "model.int8.onnx").write_bytes(b"different-content")
    with pytest.raises(FileExistsError):
        install_archive(archive, manifest, dest)
    # The existing directory must never be deleted.
    assert (dest / "model.int8.onnx").read_bytes() == b"different-content"
