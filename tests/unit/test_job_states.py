from evoblue_video_mcp.jobs import ACTIVE_JOB_STATUSES, TERMINAL_JOB_STATUSES, JobStatus


def test_terminal_and_active_states_are_disjoint_and_complete() -> None:
    assert TERMINAL_JOB_STATUSES.isdisjoint(ACTIVE_JOB_STATUSES)
    assert frozenset(JobStatus) == TERMINAL_JOB_STATUSES | ACTIVE_JOB_STATUSES

