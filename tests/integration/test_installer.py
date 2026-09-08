"""install_model orchestration: download, verify, install, activate, and failure paths."""

import asyncio
import hashlib
import io
import random
import tarfile
from pathlib import Path

import httpx
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.asr import installer as installer_module
from evoblue_video_mcp.asr import manifest as manifest_module
from evoblue_video_mcp.asr.catalog import ApprovedSource
from evoblue_video_mcp.asr.installer import NotReleasableError, _temp_path, install_model
from evoblue_video_mcp.asr.manifest import ModelFile, ModelManifest, file_set_fingerprint
from evoblue_video_mcp.storage.model_download import ModelDownloadStatus
from evoblue_video_mcp.storage.repository import (
    activate_installation,
    advance_download_operation,
    create_download_operation,
    get_active_model,
    get_download_operation,
    get_installation,
    save_installation,
    update_download_progress,
)

_MODEL = b"test-model-weights"
_URL = "https://github.com/k2-fsa/test/releases/test-model.tar.bz2"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(model_bytes: bytes = _MODEL) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        info = tarfile.TarInfo("model.int8.onnx")
        info.size = len(model_bytes)
        tar.addfile(info, io.BytesIO(model_bytes))
    return buf.getvalue()


def _make_manifest(
    model_id: str, version: str, archive: bytes, model: bytes = _MODEL
) -> ModelManifest:
    return ModelManifest.model_validate(
        {
            "model_id": model_id,
            "version": version,
            "provider": "p",
            "languages": ["zh"],
            "platforms": ["windows-x86_64"],
            "compressed_size_bytes": len(archive),
            "installed_size_bytes": len(model),
            "license": "l",
            "attribution": "a",
            "upstream_url": "https://github.com/k2-fsa/test",
            "redistribution": "upstream_only",
            "archive_format": "tar.bz2",
            "sources": [
                {
                    "url": _URL,
                    "kind": "upstream",
                    "sha256": _sha(archive),
                    "size_bytes": len(archive),
                }
            ],
            "files": [
                {
                    "name": "model.int8.onnx",
                    "size_bytes": len(model),
                    "sha256": _sha(model),
                }
            ],
        }
    )


def _releasable(
    monkeypatch, model_id: str, version: str, archive: bytes, model: bytes = _MODEL
) -> ModelManifest:
    entry = ApprovedSource(url=_URL, kind="upstream", sha256=_sha(archive), size_bytes=len(archive))
    catalog = {(model_id, version): frozenset({entry})}
    monkeypatch.setattr(manifest_module, "APPROVED_CATALOG", catalog)
    return _make_manifest(model_id, version, archive, model)


def _client(archive: bytes) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=archive)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        pass


def _chunked_client(chunks: list[bytes]) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_ChunkedStream(chunks))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_install_completes_and_activates(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)
    async with session_factory() as session:
        op = await install_model(
            session=session,
            manifest=manifest,
            http_client=_client(archive),
            models_dir=tmp_path,
        )

    assert op.status == ModelDownloadStatus.COMPLETED.value
    async with session_factory() as session:
        install = await get_installation(session, model_id="test-model", version="1.0")
        assert install is not None
        active = await get_active_model(session, model_id="test-model")
        assert active is not None and active.active_version == "1.0"
    assert (tmp_path / "test-model" / "1.0" / "model.int8.onnx").read_bytes() == _MODEL


async def test_file_set_downloads_nested_files_and_activates(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    payloads = {
        "/base/model/encoder.onnx": b"encoder",
        "/base/tokenizer/vocab.json": b'{"token": 1}',
    }
    files = tuple(
        ModelFile(
            name=local,
            source_path=remote.removeprefix("/base/"),
            size_bytes=len(body),
            sha256=_sha(body),
        )
        for (remote, body), local in zip(
            payloads.items(), ("encoder.onnx", "tokenizer/vocab.json"), strict=True
        )
    )
    fingerprint = file_set_fingerprint(files)
    base_url = "https://modelscope.cn/base"
    total = sum(len(body) for body in payloads.values())
    manifest = ModelManifest.model_validate(
        {
            "model_id": "file-set-model",
            "version": "1.0",
            "provider": "p",
            "languages": ["zh"],
            "platforms": ["windows-x86_64"],
            "compressed_size_bytes": total,
            "installed_size_bytes": total,
            "license": "Apache-2.0",
            "attribution": "test",
            "upstream_url": "https://example.com",
            "redistribution": "mirror_approved",
            "archive_format": "file-set",
            "sources": [
                {
                    "url": base_url,
                    "kind": "china-primary",
                    "sha256": fingerprint,
                    "size_bytes": total,
                }
            ],
            "files": [file.model_dump() for file in files],
        }
    )
    monkeypatch.setattr(
        manifest_module,
        "APPROVED_CATALOG",
        {
            (manifest.model_id, manifest.version): frozenset(
                {
                    ApprovedSource(
                        url=base_url,
                        kind="china-primary",
                        sha256=fingerprint,
                        size_bytes=total,
                    )
                }
            )
        },
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payloads[request.url.path])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with session_factory() as session:
        op = await install_model(
            session=session,
            manifest=manifest,
            http_client=client,
            models_dir=tmp_path,
        )
    await client.aclose()

    assert op.status == ModelDownloadStatus.COMPLETED.value
    installed = tmp_path / "file-set-model" / "1.0"
    assert (installed / "encoder.onnx").read_bytes() == b"encoder"
    assert (installed / "tokenizer" / "vocab.json").read_bytes() == b'{"token": 1}'


async def test_not_releasable_raises_without_operation(
    session_factory: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    archive = _archive()
    # No catalog entry is added, so the manifest is not releasable.
    manifest = _make_manifest("test-model", "1.0", archive, _MODEL)
    async with session_factory() as session:
        with pytest.raises(NotReleasableError):
            await install_model(
                session=session,
                manifest=manifest,
                http_client=_client(archive),
                models_dir=tmp_path,
            )


async def test_download_failure_marks_failed_and_preserves_active(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    async with session_factory() as session:
        await save_installation(
            session, model_id="test-model", version="0.9", installed_path="/old", now=1.0
        )
        await activate_installation(session, model_id="test-model", version="0.9", now=1.0)

    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)
    # Serve wrong content of the same length so the size passes but the digest fails.
    wrong = b"0" * len(archive)
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=_client(wrong), models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.FAILED.value
    assert op.error_code == "SHA256_MISMATCH"
    async with session_factory() as session:
        active = await get_active_model(session, model_id="test-model")
        assert active is not None and active.active_version == "0.9"
        assert await get_installation(session, model_id="test-model", version="1.0") is None


async def test_existing_version_dir_conflict_marks_failed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)
    # Pre-create a conflicting version directory with different content.
    version_dir = tmp_path / "test-model" / "1.0"
    version_dir.mkdir(parents=True)
    (version_dir / "model.int8.onnx").write_bytes(b"different")

    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=_client(archive), models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.FAILED.value
    assert op.error_code == "INSTALL_CONFLICT"
    # The existing directory is untouched.
    assert (version_dir / "model.int8.onnx").read_bytes() == b"different"


async def test_reuses_existing_operation_and_resumes(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)
    partial = archive[:50]

    async with session_factory() as session:
        await create_download_operation(
            session,
            operation_id="op-1",
            model_id="test-model",
            version="1.0",
            source_url=_URL,
            source_kind="upstream",
            expected_sha256=_sha(archive),
            expected_size_bytes=len(archive),
            now=1.0,
        )
        temp_path = tmp_path / ".downloads" / "test-model" / "1.0.part"
        temp_path.parent.mkdir(parents=True)
        temp_path.write_bytes(partial)
        await advance_download_operation(
            session,
            operation_id="op-1",
            to_status=ModelDownloadStatus.DOWNLOADING,
            now=2.0,
            temp_path=str(temp_path),
        )
        await update_download_progress(
            session, operation_id="op-1", downloaded_bytes=len(partial), now=3.0
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Range") == f"bytes={len(partial)}-"
        remainder = archive[len(partial):]
        return httpx.Response(
            206,
            content=remainder,
            headers={"Content-Range": f"bytes {len(partial)}-{len(archive) - 1}/{len(archive)}"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=client, models_dir=tmp_path
        )

    assert op.operation_id == "op-1"
    assert op.status == ModelDownloadStatus.COMPLETED.value
    async with session_factory() as session:
        active = await get_active_model(session, model_id="test-model")
        assert active is not None and active.active_version == "1.0"


async def test_range_ignored_restarts_and_completes(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)

    async with session_factory() as session:
        await create_download_operation(
            session,
            operation_id="op-1",
            model_id="test-model",
            version="1.0",
            source_url=_URL,
            source_kind="upstream",
            expected_sha256=_sha(archive),
            expected_size_bytes=len(archive),
            now=1.0,
        )
        temp_path = tmp_path / ".downloads" / "test-model" / "1.0.part"
        temp_path.parent.mkdir(parents=True)
        temp_path.write_bytes(b"01234")
        await advance_download_operation(
            session,
            operation_id="op-1",
            to_status=ModelDownloadStatus.DOWNLOADING,
            now=2.0,
            temp_path=str(temp_path),
        )
        await update_download_progress(session, operation_id="op-1", downloaded_bytes=5, now=3.0)

    # Server ignores Range and returns the full resource with a 200.
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=_client(archive), models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.COMPLETED.value
    assert (tmp_path / ".downloads" / "test-model" / "1.0.part").read_bytes() == archive


async def test_cancellation_preserves_checkpoint(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    model = random.Random(0).randbytes(2000)
    archive = _archive(model)
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive, model)

    chunk = 100
    chunks = [archive[i : i + chunk] for i in range(0, len(archive), chunk)]
    assert len(chunks) >= 4
    calls = {"n": 0}

    async def cancel_check() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    async with session_factory() as session:
        op = await install_model(
            session=session,
            manifest=manifest,
            http_client=_chunked_client(chunks),
            models_dir=tmp_path,
            cancel_check=cancel_check,
        )

    assert op.status == ModelDownloadStatus.CANCELLED.value
    async with session_factory() as session:
        final = await get_download_operation(session, operation_id=op.operation_id)
        assert final is not None
        partial = tmp_path / ".downloads" / "test-model" / "1.0.part"
        assert final.downloaded_bytes == partial.stat().st_size
        assert 0 < final.downloaded_bytes < len(archive)
        assert await get_installation(session, model_id="test-model", version="1.0") is None


async def test_concurrent_requests_have_single_executor(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=archive)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with session_factory() as s1, session_factory() as s2:
        t1 = asyncio.create_task(
            install_model(session=s1, manifest=manifest, http_client=client, models_dir=tmp_path)
        )
        t2 = asyncio.create_task(
            install_model(session=s2, manifest=manifest, http_client=client, models_dir=tmp_path)
        )
        r1, r2 = await asyncio.gather(t1, t2)

    assert calls["n"] == 1
    assert r1.status == ModelDownloadStatus.COMPLETED.value
    assert r2.status == ModelDownloadStatus.COMPLETED.value
    assert r1.operation_id == r2.operation_id


async def test_network_interruption_resumes_with_validator(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    model = random.Random(1).randbytes(2000)
    archive = _archive(model)
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive, model)
    chunk = 100
    chunks = [archive[i : i + chunk] for i in range(0, len(archive), chunk)]
    prefix = chunks[:2]
    partial_len = sum(len(c) for c in prefix)

    class _FailStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for piece in prefix:
                yield piece
            raise httpx.RemoteProtocolError("connection reset")

        async def aclose(self) -> None:
            pass

    async def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"ETag": '"v1"'}, stream=_FailStream())

    fail_client = httpx.AsyncClient(transport=httpx.MockTransport(fail_handler))
    async with session_factory() as session:
        op1 = await install_model(
            session=session, manifest=manifest, http_client=fail_client, models_dir=tmp_path
        )

    assert op1.status == ModelDownloadStatus.FAILED.value
    assert op1.error_code == "DOWNLOAD_FAILED"
    # The partial is preserved for resume.
    partial = tmp_path / ".downloads" / "test-model" / "1.0.part"
    assert partial.stat().st_size == partial_len

    async def resume_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Range") == f"bytes={partial_len}-"
        assert request.headers.get("If-Range") == '"v1"'
        remainder = archive[partial_len:]
        return httpx.Response(
            206,
            content=remainder,
            headers={
                "Content-Range": f"bytes {partial_len}-{len(archive) - 1}/{len(archive)}",
            },
        )

    resume_client = httpx.AsyncClient(transport=httpx.MockTransport(resume_handler))
    async with session_factory() as session:
        op2 = await install_model(
            session=session, manifest=manifest, http_client=resume_client, models_dir=tmp_path
        )

    assert op2.status == ModelDownloadStatus.COMPLETED.value


async def test_crash_between_checkpoints_recovers_not_deletes(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)
    checkpoint = 100  # DB recorded only 100 bytes
    on_disk = 200  # but the file actually holds 200 bytes (crash between checkpoints)

    async with session_factory() as session:
        await create_download_operation(
            session,
            operation_id="op",
            model_id="test-model",
            version="1.0",
            source_url=_URL,
            source_kind="upstream",
            expected_sha256=_sha(archive),
            expected_size_bytes=len(archive),
            now=1.0,
        )
        temp_path = tmp_path / ".downloads" / "test-model" / "1.0.part"
        temp_path.parent.mkdir(parents=True)
        temp_path.write_bytes(archive[:on_disk])
        await advance_download_operation(
            session,
            operation_id="op",
            to_status=ModelDownloadStatus.DOWNLOADING,
            now=2.0,
            temp_path=str(temp_path),
        )
        await update_download_progress(
            session, operation_id="op", downloaded_bytes=checkpoint, now=3.0
        )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Range") == f"bytes={on_disk}-"
        remainder = archive[on_disk:]
        return httpx.Response(
            206,
            content=remainder,
            headers={"Content-Range": f"bytes {on_disk}-{len(archive) - 1}/{len(archive)}"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=client, models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.COMPLETED.value


async def test_final_step_failure_leaves_active_unchanged(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)

    async with session_factory() as session:
        await save_installation(
            session, model_id="test-model", version="0.9", installed_path="/old", now=1.0
        )
        await activate_installation(session, model_id="test-model", version="0.9", now=1.0)

    async def boom(**kwargs) -> None:
        raise RuntimeError("final transition failed")

    monkeypatch.setattr(installer_module, "complete_installation", boom)
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=_client(archive), models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.FAILED.value
    async with session_factory() as session:
        active = await get_active_model(session, model_id="test-model")
        assert active is not None and active.active_version == "0.9"


def test_partial_path_isolates_colliding_model_version_pairs() -> None:
    root = Path("/models")
    p1 = _temp_path("a-b", "c", root)
    p2 = _temp_path("a", "b-c", root)
    assert p1 != p2
    assert p1 == root / ".downloads" / "a-b" / "c.part"
    assert p2 == root / ".downloads" / "a" / "b-c.part"


async def test_commit_failure_marks_operation_failed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _releasable(monkeypatch, "test-model", "1.0", archive)
    real_complete = installer_module.complete_installation

    async def failing_complete(session: AsyncSession, **kwargs):
        original_commit = session.commit

        async def boom() -> None:
            raise OperationalError("COMMIT", {}, Exception("disk full"))

        session.commit = boom  # type: ignore[method-assign]
        try:
            return await real_complete(session, **kwargs)
        finally:
            session.commit = original_commit  # type: ignore[method-assign]

    monkeypatch.setattr(installer_module, "complete_installation", failing_complete)
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=_client(archive), models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.FAILED.value
    assert op.error_code == "INTERNAL"


def _two_source_manifest(archive: bytes, url2: str = _URL) -> ModelManifest:
    url1 = "https://mirror.invalid/test-model.tar.bz2"
    return ModelManifest.model_validate(
        {
            "model_id": "test-model",
            "version": "1.0",
            "provider": "p",
            "languages": ["zh"],
            "platforms": ["windows-x86_64"],
            "compressed_size_bytes": len(archive),
            "installed_size_bytes": len(_MODEL),
            "license": "l",
            "attribution": "a",
            "upstream_url": "https://github.com/k2-fsa/test",
            "redistribution": "upstream_only",
            "archive_format": "tar.bz2",
            "sources": [
                {
                    "url": url1,
                    "kind": "upstream",
                    "sha256": _sha(archive),
                    "size_bytes": len(archive),
                },
                {
                    "url": url2,
                    "kind": "upstream",
                    "sha256": _sha(archive),
                    "size_bytes": len(archive),
                },
            ],
            "files": [
                {"name": "model.int8.onnx", "size_bytes": len(_MODEL), "sha256": _sha(_MODEL)}
            ],
        }
    )


def _approve_two_sources(monkeypatch, archive: bytes, url2: str = _URL) -> None:
    url1 = "https://mirror.invalid/test-model.tar.bz2"
    catalog = {
        ("test-model", "1.0"): frozenset(
            {
                ApprovedSource(
                    url=url1, kind="upstream", sha256=_sha(archive), size_bytes=len(archive)
                ),
                ApprovedSource(
                    url=url2, kind="upstream", sha256=_sha(archive), size_bytes=len(archive)
                ),
            }
        )
    }
    monkeypatch.setattr(manifest_module, "APPROVED_CATALOG", catalog)


async def test_falls_back_to_next_source_on_download_failure(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _two_source_manifest(archive)
    _approve_two_sources(monkeypatch, archive)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mirror.invalid":
            return httpx.Response(404)
        return httpx.Response(200, content=archive)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=client, models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.COMPLETED.value
    # The operation records the source that actually served the artifact.
    assert op.source_url == _URL
    assert op.source_kind == "upstream"
    async with session_factory() as session:
        active = await get_active_model(session, model_id="test-model")
        assert active is not None and active.active_version == "1.0"


async def test_all_sources_fail_marks_failed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _two_source_manifest(archive)
    _approve_two_sources(monkeypatch, archive)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=client, models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.FAILED.value
    assert op.error_code == "DOWNLOAD_FAILED"
    async with session_factory() as session:
        assert await get_installation(session, model_id="test-model", version="1.0") is None


async def test_fallback_does_not_resume_a_corrupt_prefix(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    model = _MODEL
    archive = _archive(model)
    manifest = _two_source_manifest(archive)
    _approve_two_sources(monkeypatch, archive)

    corrupt_prefix = b"XXXX-corrupt-prefix"

    class _FailStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield corrupt_prefix
            raise httpx.RemoteProtocolError("connection reset")

        async def aclose(self) -> None:
            pass

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mirror.invalid":
            return httpx.Response(200, stream=_FailStream())
        return httpx.Response(200, content=archive)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=client, models_dir=tmp_path
        )

    # If the fallback resumed from the corrupt prefix, the installed file would be
    # "corrupt-prefix + correct-suffix" and digest-verify would fail. COMPLETED plus
    # the exact model bytes proves the fallback restarted from zero.
    assert op.status == ModelDownloadStatus.COMPLETED.value
    installed = tmp_path / "test-model" / "1.0" / "model.int8.onnx"
    assert installed.read_bytes() == model


async def test_fallback_on_digest_mismatch(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, monkeypatch
) -> None:
    archive = _archive()
    manifest = _two_source_manifest(archive)
    _approve_two_sources(monkeypatch, archive)

    wrong = b"0" * len(archive)  # same size, wrong content -> digest mismatch

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mirror.invalid":
            return httpx.Response(200, content=wrong)
        return httpx.Response(200, content=archive)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with session_factory() as session:
        op = await install_model(
            session=session, manifest=manifest, http_client=client, models_dir=tmp_path
        )

    assert op.status == ModelDownloadStatus.COMPLETED.value
    assert op.source_url == _URL
