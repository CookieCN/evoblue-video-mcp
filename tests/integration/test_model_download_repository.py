"""Model download repository: active pointer, per-version install, revision CAS."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.storage.model_download import ModelDownloadStatus, TransitionError
from evoblue_video_mcp.storage.repository import (
    StaleRevisionError,
    activate_installation,
    advance_download_operation,
    complete_installation,
    create_download_operation,
    find_active_download_operation,
    get_active_model,
    get_download_operation,
    get_installation,
    restart_download,
    save_installation,
    switch_download_source,
    uninstall_model,
    update_download_progress,
)

_SOURCE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/m.tar.bz2"
)


async def _create_downloading_operation(
    session: AsyncSession, operation_id: str = "op"
) -> None:
    await create_download_operation(
        session,
        operation_id=operation_id,
        model_id="m",
        version="v1",
        source_url=_SOURCE_URL,
        source_kind="upstream",
        expected_sha256="a" * 64,
        expected_size_bytes=1000,
        now=1.0,
    )
    await advance_download_operation(
        session, operation_id=operation_id, to_status=ModelDownloadStatus.DOWNLOADING, now=2.0
    )


async def test_active_pointer_and_install_are_separate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await save_installation(
            sess, model_id="m", version="v1", installed_path="/m/v1", now=1.0
        )
        await activate_installation(sess, model_id="m", version="v1", now=1.0)
        await create_download_operation(
            sess,
            operation_id="op-v2",
            model_id="m",
            version="v2",
            source_url=_SOURCE_URL,
            source_kind="upstream",
            expected_sha256="a" * 64,
            expected_size_bytes=100,
            now=2.0,
        )

    async with session_factory() as sess:
        active = await get_active_model(sess, model_id="m")
        assert active is not None
        assert active.active_version == "v1"
        assert await get_installation(sess, model_id="m", version="v2") is None


async def test_activate_missing_version_fails_without_changing_active(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await save_installation(
            sess, model_id="m", version="v1", installed_path="/m/v1", now=1.0
        )
        await activate_installation(sess, model_id="m", version="v1", now=1.0)
        with pytest.raises(KeyError):
            await activate_installation(sess, model_id="m", version="v2", now=2.0)

    async with session_factory() as sess:
        active = await get_active_model(sess, model_id="m")
        assert active is not None
        assert active.active_version == "v1"


async def test_update_progress_is_monotonic(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        await update_download_progress(sess, operation_id="op", downloaded_bytes=500, now=3.0)
        with pytest.raises(ValueError):
            await update_download_progress(sess, operation_id="op", downloaded_bytes=50, now=4.0)


async def test_update_progress_rejects_out_of_range(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        with pytest.raises(ValueError):
            await update_download_progress(
                sess, operation_id="op", downloaded_bytes=2000, now=3.0
            )


async def test_update_progress_requires_downloading(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await create_download_operation(
            sess,
            operation_id="op",
            model_id="m",
            version="v1",
            source_url=_SOURCE_URL,
            source_kind="upstream",
            expected_sha256="a" * 64,
            expected_size_bytes=1000,
            now=1.0,
        )
        with pytest.raises(ValueError):
            await update_download_progress(
                sess, operation_id="op", downloaded_bytes=10, now=2.0
            )


async def test_update_progress_rejects_stale_revision(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        await update_download_progress(sess, operation_id="op", downloaded_bytes=100, now=3.0)
        with pytest.raises(StaleRevisionError):
            await update_download_progress(
                sess,
                operation_id="op",
                downloaded_bytes=200,
                now=4.0,
                expected_revision=1,
            )


async def test_advance_rejects_stale_revision(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        await update_download_progress(
            sess, operation_id="op", downloaded_bytes=1000, now=3.0
        )
        with pytest.raises(StaleRevisionError):
            await advance_download_operation(
                sess,
                operation_id="op",
                to_status=ModelDownloadStatus.VERIFYING,
                now=4.0,
                expected_revision=0,
            )


async def test_illegal_transition_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await create_download_operation(
            sess,
            operation_id="op",
            model_id="m",
            version="v1",
            source_url=_SOURCE_URL,
            source_kind="upstream",
            expected_sha256="a" * 64,
            expected_size_bytes=1000,
            now=1.0,
        )
        with pytest.raises(TransitionError):
            await advance_download_operation(
                sess, operation_id="op", to_status=ModelDownloadStatus.COMPLETED, now=2.0
            )


async def test_restart_download_zeroes_bytes_and_validators(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        await update_download_progress(
            sess,
            operation_id="op",
            downloaded_bytes=500,
            now=3.0,
            etag='"abc"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
        )
        await restart_download(sess, operation_id="op", now=4.0)

    async with session_factory() as sess:
        op = await get_download_operation(sess, operation_id="op")
        assert op is not None
        assert op.downloaded_bytes == 0
        assert op.etag is None
        assert op.last_modified is None
        assert op.status == ModelDownloadStatus.DOWNLOADING.value


async def test_restart_download_rejects_non_downloading(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        await update_download_progress(sess, operation_id="op", downloaded_bytes=1000, now=3.0)
        await advance_download_operation(
            sess, operation_id="op", to_status=ModelDownloadStatus.VERIFYING, now=4.0
        )
        with pytest.raises(ValueError):
            await restart_download(sess, operation_id="op", now=5.0)


async def test_find_active_returns_only_non_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        active = await find_active_download_operation(sess, model_id="m")
        assert active is not None and active.operation_id == "op"

        await advance_download_operation(
            sess, operation_id="op", to_status=ModelDownloadStatus.CANCELLED, now=3.0
        )
        assert await find_active_download_operation(sess, model_id="m") is None


async def _create_installing_operation(session: AsyncSession) -> None:
    await _create_downloading_operation(session)
    await update_download_progress(session, operation_id="op", downloaded_bytes=1000, now=3.0)
    await advance_download_operation(
        session, operation_id="op", to_status=ModelDownloadStatus.VERIFYING, now=4.0
    )
    await advance_download_operation(
        session, operation_id="op", to_status=ModelDownloadStatus.INSTALLING, now=5.0
    )


async def test_complete_installation_records_install_active_and_completed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_installing_operation(sess)
        op = await complete_installation(
            sess,
            operation_id="op",
            model_id="m",
            version="v1",
            installed_path="/m/v1",
            now=6.0,
        )
        assert op.status == ModelDownloadStatus.COMPLETED.value

    async with session_factory() as sess:
        install = await get_installation(sess, model_id="m", version="v1")
        assert install is not None and install.installed_path == "/m/v1"
        active = await get_active_model(sess, model_id="m")
        assert active is not None and active.active_version == "v1"


async def test_complete_installation_rejects_mismatched_model_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_installing_operation(sess)  # operation "op" is for ("m", "v1")
        with pytest.raises(ValueError):
            await complete_installation(
                sess,
                operation_id="op",
                model_id="other",
                version="v9",
                installed_path="/x",
                now=6.0,
            )


async def test_complete_installation_rolls_back_on_stale_revision(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    from evoblue_video_mcp.storage import repository

    async with session_factory() as sess:
        await save_installation(sess, model_id="m", version="v0", installed_path="/v0", now=1.0)
        await activate_installation(sess, model_id="m", version="v0", now=1.0)
        await _create_installing_operation(sess)

    real_get = repository.get_download_operation

    async def stale_get(session: AsyncSession, *, operation_id: str):
        op = await real_get(session, operation_id=operation_id)
        if op is not None:
            session.expunge(op)  # detach so the stale revision is not auto-flushed
            op.revision -= 1  # present a stale revision so the CAS misses
        return op

    monkeypatch.setattr(repository, "get_download_operation", stale_get)

    async with session_factory() as sess:
        with pytest.raises(StaleRevisionError):
            await complete_installation(
                sess,
                operation_id="op",
                model_id="m",
                version="v1",
                installed_path="/m/v1",
                now=6.0,
            )

    # The rollback leaves both the active pointer and the install record untouched.
    async with session_factory() as sess:
        active = await get_active_model(sess, model_id="m")
        assert active is not None and active.active_version == "v0"
        assert await get_installation(sess, model_id="m", version="v1") is None


async def test_switch_download_source_updates_url_and_clears_validators(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        await update_download_progress(
            sess,
            operation_id="op",
            downloaded_bytes=500,
            now=3.0,
            etag='"abc"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
        )
        await switch_download_source(
            sess,
            operation_id="op",
            source_url="https://hf-mirror.com/m.tar.bz2",
            source_kind="upstream",
            now=4.0,
        )

    async with session_factory() as sess:
        op = await get_download_operation(sess, operation_id="op")
        assert op is not None
        assert op.source_url == "https://hf-mirror.com/m.tar.bz2"
        assert op.source_kind == "upstream"
        assert op.downloaded_bytes == 0
        assert op.etag is None
        assert op.last_modified is None
        assert op.status == ModelDownloadStatus.DOWNLOADING.value


async def test_switch_download_source_rejects_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _create_downloading_operation(sess)
        await update_download_progress(sess, operation_id="op", downloaded_bytes=1000, now=3.0)
        await advance_download_operation(
            sess, operation_id="op", to_status=ModelDownloadStatus.VERIFYING, now=4.0
        )
        with pytest.raises(ValueError):
            await switch_download_source(
                sess,
                operation_id="op",
                source_url="https://x/m.tar.bz2",
                source_kind="upstream",
                now=5.0,
            )


async def test_uninstall_model_removes_all_records_and_returns_paths(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await save_installation(sess, model_id="m", version="v1", installed_path="/m/v1", now=1.0)
        await activate_installation(sess, model_id="m", version="v1", now=1.0)
        await create_download_operation(
            sess,
            operation_id="op",
            model_id="m",
            version="v1",
            source_url=_SOURCE_URL,
            source_kind="upstream",
            expected_sha256="a" * 64,
            expected_size_bytes=1000,
            now=2.0,
        )
        paths = await uninstall_model(sess, model_id="m")

    assert paths == ["/m/v1"]

    async with session_factory() as sess:
        assert await get_installation(sess, model_id="m", version="v1") is None
        assert await get_active_model(sess, model_id="m") is None
        assert await get_download_operation(sess, operation_id="op") is None
