"""State-machine transition rules: legal and illegal moves are enforced."""

import pytest

from evoblue_video_mcp.jobs import JobStatus
from evoblue_video_mcp.jobs.states import TERMINAL_JOB_STATUSES
from evoblue_video_mcp.jobs.transitions import (
    RUNNING_STATES,
    TransitionError,
    is_valid_transition,
    validate_transition,
)


def test_linear_pipeline_forward_moves_are_valid() -> None:
    chain = [
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
    ]
    for src, dst in chain:
        assert is_valid_transition(src, dst), f"{src} -> {dst} should be valid"


def test_running_state_can_pause_to_retry_or_fail() -> None:
    for state in RUNNING_STATES:
        assert is_valid_transition(state, JobStatus.RETRY_WAIT)
        assert is_valid_transition(state, JobStatus.FAILED)


def test_retry_wait_reenters_any_running_stage_or_terminates() -> None:
    for state in RUNNING_STATES:
        assert is_valid_transition(JobStatus.RETRY_WAIT, state)
    assert is_valid_transition(JobStatus.RETRY_WAIT, JobStatus.FAILED)
    assert is_valid_transition(JobStatus.RETRY_WAIT, JobStatus.CANCELLED)


def test_queued_can_be_cancelled_or_failed_before_start() -> None:
    assert is_valid_transition(JobStatus.QUEUED, JobStatus.CANCELLED)
    assert is_valid_transition(JobStatus.QUEUED, JobStatus.FAILED)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        # Skipping stages is illegal.
        (JobStatus.QUEUED, JobStatus.COMPLETED),
        (JobStatus.QUEUED, JobStatus.INDEXING),
        (JobStatus.FETCHING_METADATA, JobStatus.INDEXING),
        (JobStatus.FETCHING_METADATA, JobStatus.CLEANING_TRANSCRIPT),
        # A queued job has not started, so it cannot pause into retry_wait.
        (JobStatus.QUEUED, JobStatus.RETRY_WAIT),
        # Reversing direction is illegal.
        (JobStatus.CHUNKING, JobStatus.FETCHING_METADATA),
        (JobStatus.GENERATING_REPORT, JobStatus.CHUNKING),
        # Terminal states have no outgoing edges.
        (JobStatus.COMPLETED, JobStatus.INDEXING),
        (JobStatus.FAILED, JobStatus.QUEUED),
        (JobStatus.CANCELLED, JobStatus.FETCHING_METADATA),
    ],
)
def test_illegal_moves_are_rejected(src: JobStatus, dst: JobStatus) -> None:
    assert not is_valid_transition(src, dst)


def test_terminal_states_have_no_outgoing_edges() -> None:
    for terminal in TERMINAL_JOB_STATUSES:
        for other in JobStatus:
            assert not is_valid_transition(terminal, other), f"{terminal} must be a sink"


def test_validate_transition_raises_on_illegal_move() -> None:
    with pytest.raises(TransitionError):
        validate_transition(JobStatus.QUEUED, JobStatus.COMPLETED)


def test_validate_transition_passes_on_legal_move() -> None:
    validate_transition(JobStatus.QUEUED, JobStatus.FETCHING_METADATA)
