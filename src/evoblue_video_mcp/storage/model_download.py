"""Model download state machine shared by the installer and repository."""

from enum import StrEnum


class ModelDownloadStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    INSTALLING = "installing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_DOWNLOAD_STATUSES = frozenset(
    {ModelDownloadStatus.COMPLETED, ModelDownloadStatus.FAILED, ModelDownloadStatus.CANCELLED}
)

# Statuses that represent an in-flight operation; at most one per model (DB partial
# unique index) so repeated install requests reuse it rather than racing.
ACTIVE_DOWNLOAD_STATUSES = frozenset(
    {
        ModelDownloadStatus.PENDING,
        ModelDownloadStatus.DOWNLOADING,
        ModelDownloadStatus.VERIFYING,
        ModelDownloadStatus.INSTALLING,
    }
)


class TransitionError(ValueError):
    """Raised when a download status transition violates the state machine."""


_LINEAR_CHAIN: tuple[tuple[ModelDownloadStatus, ModelDownloadStatus], ...] = (
    (ModelDownloadStatus.PENDING, ModelDownloadStatus.DOWNLOADING),
    (ModelDownloadStatus.DOWNLOADING, ModelDownloadStatus.VERIFYING),
    (ModelDownloadStatus.VERIFYING, ModelDownloadStatus.INSTALLING),
    (ModelDownloadStatus.INSTALLING, ModelDownloadStatus.COMPLETED),
)

_ABORTABLE = frozenset(
    {
        ModelDownloadStatus.PENDING,
        ModelDownloadStatus.DOWNLOADING,
        ModelDownloadStatus.VERIFYING,
        ModelDownloadStatus.INSTALLING,
    }
)


def _build_allowed() -> dict[ModelDownloadStatus, frozenset[ModelDownloadStatus]]:
    allowed: dict[ModelDownloadStatus, set[ModelDownloadStatus]] = {
        status: set() for status in ModelDownloadStatus
    }
    for src, dst in _LINEAR_CHAIN:
        allowed[src].add(dst)
    for status in _ABORTABLE:
        allowed[status].add(ModelDownloadStatus.FAILED)
        allowed[status].add(ModelDownloadStatus.CANCELLED)
    return {status: frozenset(targets) for status, targets in allowed.items()}


ALLOWED_DOWNLOAD_TRANSITIONS: dict[ModelDownloadStatus, frozenset[ModelDownloadStatus]] = (
    _build_allowed()
)


def validate_transition(src: ModelDownloadStatus, dst: ModelDownloadStatus) -> None:
    if dst not in ALLOWED_DOWNLOAD_TRANSITIONS[src]:
        raise TransitionError(f"Illegal download transition: {src.value} -> {dst.value}")
