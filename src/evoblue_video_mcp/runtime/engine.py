"""Local Engine startup recovery."""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.storage.repository import (
    fail_exhausted_retries,
    land_dangling_cancellations,
    recover_stale_jobs,
)


@dataclass(frozen=True)
class RecoveryReport:
    """What a startup recovery sweep changed."""

    released_leases: list[str] = field(default_factory=list)
    failed_exhausted: list[str] = field(default_factory=list)
    landed_cancellations: list[str] = field(default_factory=list)


async def recover_on_startup(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: float,
) -> RecoveryReport:
    """Release stale leases, fail exhausted retries, and land dangling
    cancellations before workers start (F4, feedback #15: a parked job must
    never carry an unconsumed cancel across a restart)."""
    async with session_factory() as sess:
        released = await recover_stale_jobs(sess, now=now)
        failed = await fail_exhausted_retries(sess, now=now)
        landed = await land_dangling_cancellations(sess, now=now)
    return RecoveryReport(
        released_leases=[job.job_id for job in released],
        failed_exhausted=[job.job_id for job in failed],
        landed_cancellations=[job.job_id for job in landed],
    )
