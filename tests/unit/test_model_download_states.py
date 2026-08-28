"""Model download state machine: statuses and legal transitions."""

import pytest

from evoblue_video_mcp.storage.model_download import (
    ModelDownloadStatus,
    TransitionError,
    validate_transition,
)


def test_linear_chain_is_legal() -> None:
    validate_transition(ModelDownloadStatus.PENDING, ModelDownloadStatus.DOWNLOADING)
    validate_transition(ModelDownloadStatus.DOWNLOADING, ModelDownloadStatus.VERIFYING)
    validate_transition(ModelDownloadStatus.VERIFYING, ModelDownloadStatus.INSTALLING)
    validate_transition(ModelDownloadStatus.INSTALLING, ModelDownloadStatus.COMPLETED)


def test_abortable_states_can_fail_and_cancel() -> None:
    for status in (
        ModelDownloadStatus.DOWNLOADING,
        ModelDownloadStatus.VERIFYING,
        ModelDownloadStatus.INSTALLING,
    ):
        validate_transition(status, ModelDownloadStatus.FAILED)
        validate_transition(status, ModelDownloadStatus.CANCELLED)


def test_terminal_states_have_no_exit() -> None:
    for terminal in (
        ModelDownloadStatus.COMPLETED,
        ModelDownloadStatus.FAILED,
        ModelDownloadStatus.CANCELLED,
    ):
        with pytest.raises(TransitionError):
            validate_transition(terminal, ModelDownloadStatus.DOWNLOADING)


def test_skipped_stage_is_illegal() -> None:
    with pytest.raises(TransitionError):
        validate_transition(ModelDownloadStatus.PENDING, ModelDownloadStatus.COMPLETED)
