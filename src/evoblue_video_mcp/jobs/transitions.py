"""State-machine transition rules shared by the worker and the API layer."""

from evoblue_video_mcp.jobs.states import TERMINAL_JOB_STATUSES, JobStatus


class TransitionError(ValueError):
    """Raised when a job state transition violates the state machine."""


# States a job occupies while actively executing a pipeline stage.
RUNNING_STATES = frozenset(JobStatus) - TERMINAL_JOB_STATUSES - {
    JobStatus.QUEUED,
    JobStatus.RETRY_WAIT,
}

_LINEAR_CHAIN: tuple[tuple[JobStatus, JobStatus], ...] = (
    (JobStatus.QUEUED, JobStatus.FETCHING_METADATA),
    (JobStatus.FETCHING_METADATA, JobStatus.FETCHING_SUBTITLES),
    (JobStatus.FETCHING_SUBTITLES, JobStatus.DOWNLOADING_AUDIO),
    (JobStatus.FETCHING_SUBTITLES, JobStatus.CLEANING_TRANSCRIPT),
    (JobStatus.DOWNLOADING_AUDIO, JobStatus.TRANSCRIBING),
    (JobStatus.TRANSCRIBING, JobStatus.CLEANING_TRANSCRIPT),
    (JobStatus.CLEANING_TRANSCRIPT, JobStatus.CHUNKING),
    (JobStatus.CHUNKING, JobStatus.SUMMARIZING_CHUNKS),
    (JobStatus.SUMMARIZING_CHUNKS, JobStatus.GENERATING_REPORT),
    (JobStatus.GENERATING_REPORT, JobStatus.INDEXING),
    (JobStatus.INDEXING, JobStatus.COMPLETED),
)


def _build_allowed() -> dict[JobStatus, frozenset[JobStatus]]:
    allowed: dict[JobStatus, set[JobStatus]] = {state: set() for state in JobStatus}

    for src, dst in _LINEAR_CHAIN:
        allowed[src].add(dst)

    # A running stage may pause into retry_wait on a transient error or fail outright.
    for state in RUNNING_STATES:
        allowed[state].add(JobStatus.RETRY_WAIT)
        allowed[state].add(JobStatus.FAILED)

    # A queued job can be rejected (validation) or cancelled before it starts.
    allowed[JobStatus.QUEUED].add(JobStatus.FAILED)
    allowed[JobStatus.QUEUED].add(JobStatus.CANCELLED)

    # retry_wait re-enters the stage it paused on, or terminates permanently.
    allowed[JobStatus.RETRY_WAIT] = set(RUNNING_STATES) | {
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }

    return {state: frozenset(targets) for state, targets in allowed.items()}


ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = _build_allowed()


def is_valid_transition(src: JobStatus, dst: JobStatus) -> bool:
    return dst in ALLOWED_TRANSITIONS[src]


def validate_transition(src: JobStatus, dst: JobStatus) -> None:
    if not is_valid_transition(src, dst):
        raise TransitionError(f"Illegal job transition: {src.value} -> {dst.value}")
