"""Artifact registration commits atomically with the job state transition."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.storage.models import JobArtifact
from evoblue_video_mcp.storage.repository import (
    LeaseLostError,
    claim_job,
    commit_artifact_and_advance,
    enqueue_job,
    recover_stale_jobs,
    save_artifact_inline,
)


async def _enqueue(session: AsyncSession) -> None:
    await enqueue_job(
        session,
        job_id="job-1",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        request_fingerprint="fp-1",
        config_fingerprint="cfg-1",
        now=1000.0,
    )


async def _claim(session: AsyncSession, now: float = 1000.0) -> None:
    await claim_job(session, job_id="job-1", owner="w1", lease_seconds=30.0, now=now)


async def test_commit_artifact_and_advance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        await _claim(sess)

    async with session_factory() as sess:
        job = await commit_artifact_and_advance(
            sess,
            job_id="job-1",
            owner="w1",
            to_status=JobStatus.FETCHING_SUBTITLES,
            now=1010.0,
            lease_seconds=30.0,
            stage="fetching_metadata",
            artifact_type="video_metadata",
            input_fingerprint="meta-fp",
            schema_version=1,
            storage_kind="inline_json",
            payload_json='{"title": "Test"}',
        )
        assert job.status == JobStatus.FETCHING_SUBTITLES.value

    async with session_factory() as sess:
        artifacts = (
            await sess.scalars(select(JobArtifact).where(JobArtifact.job_id == "job-1"))
        ).all()
        assert len(artifacts) == 1
        assert artifacts[0].artifact_type == "video_metadata"
        assert artifacts[0].payload_json == '{"title": "Test"}'


async def test_artifact_register_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        await _claim(sess)
        await commit_artifact_and_advance(
            sess,
            job_id="job-1",
            owner="w1",
            to_status=JobStatus.FETCHING_SUBTITLES,
            now=1010.0,
            lease_seconds=30.0,
            stage="fetching_metadata",
            artifact_type="video_metadata",
            input_fingerprint="meta-fp",
            schema_version=1,
            storage_kind="inline_json",
            payload_json='{"title": "Test"}',
        )
        # Re-register the same artifact while advancing to the next stage.
        await commit_artifact_and_advance(
            sess,
            job_id="job-1",
            owner="w1",
            to_status=JobStatus.CLEANING_TRANSCRIPT,
            now=1020.0,
            lease_seconds=30.0,
            stage="fetching_subtitles",
            artifact_type="video_metadata",
            input_fingerprint="meta-fp",
            schema_version=1,
            storage_kind="inline_json",
            payload_json='{"title": "Test"}',
        )

    async with session_factory() as sess:
        artifacts = (
            await sess.scalars(select(JobArtifact).where(JobArtifact.job_id == "job-1"))
        ).all()
        assert len(artifacts) == 1


async def test_commit_artifact_lease_lost_rolls_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await _enqueue(sess)
        await claim_job(sess, job_id="job-1", owner="w1", lease_seconds=10.0, now=1000.0)

    # w1's lease expires and w2 takes over.
    async with session_factory() as sess:
        await recover_stale_jobs(sess, now=1020.0)
        await claim_job(sess, job_id="job-1", owner="w2", lease_seconds=30.0, now=1020.0)

    async with session_factory() as sess:
        with pytest.raises(LeaseLostError):
            await commit_artifact_and_advance(
                sess,
                job_id="job-1",
                owner="w1",
                to_status=JobStatus.FETCHING_SUBTITLES,
                now=1025.0,
                lease_seconds=30.0,
                stage="fetching_metadata",
                artifact_type="video_metadata",
                input_fingerprint="meta-fp",
                schema_version=1,
                storage_kind="inline_json",
                payload_json='{"title": "Test"}',
            )

    async with session_factory() as sess:
        artifacts = (
            await sess.scalars(select(JobArtifact).where(JobArtifact.job_id == "job-1"))
        ).all()
        assert artifacts == []


async def test_save_artifact_inline_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as sess:
        await enqueue_job(
            sess,
            job_id="job-1",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            request_fingerprint="fp-1",
            config_fingerprint="cfg",
            now=1000.0,
        )
        await claim_job(sess, job_id="job-1", owner="w1", lease_seconds=30.0, now=1000.0)
        await save_artifact_inline(
            sess,
            job_id="job-1",
            owner="w1",
            now=1000.0,
            stage="summarizing_chunks",
            artifact_type="chunk_summary",
            input_fingerprint="fp",
            schema_version=1,
            payload_json="summary",
        )
        await save_artifact_inline(
            sess,
            job_id="job-1",
            owner="w1",
            now=1000.0,
            stage="summarizing_chunks",
            artifact_type="chunk_summary",
            input_fingerprint="fp",
            schema_version=1,
            payload_json="summary",
        )

    async with session_factory() as sess:
        artifacts = (
            await sess.scalars(select(JobArtifact).where(JobArtifact.job_id == "job-1"))
        ).all()
        assert len(artifacts) == 1
