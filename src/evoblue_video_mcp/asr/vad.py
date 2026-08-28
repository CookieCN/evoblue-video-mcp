"""Bundled Silero VAD: a managed runtime dependency of the sherpa-onnx tiers.

The recognition archives (Lite Zipformer / Standard SenseVoice) ship no VAD
model, yet long-audio segmentation requires one. The VAD is therefore delivered
inside the package (``asr/assets/``), pinned by SHA-256 and verified before any
sherpa provider may register — a missing or corrupted asset must fail
registration up front, not a transcription mid-job.

The whole resource path sits inside a fault boundary: every ordinary exception
(missing asset package, unreadable file, failed materialization) is logged
under a stable error code and reported as ``None``, because registration runs
before the app lifespan and one broken install must never block engine
startup. ``None`` simply means "sherpa tiers not ready".

MIT licensed; redistribution approved with attribution (see
``THIRD_PARTY_NOTICES.md`` and ``docs/ASR_MODEL_LICENSES.md``).
"""

import hashlib
import logging
from contextlib import AbstractContextManager, suppress
from importlib.resources import as_file, files
from pathlib import Path

logger = logging.getLogger(__name__)

_ASSET_PACKAGE = "evoblue_video_mcp.asr.assets"
_ASSET_NAME = "silero_vad.onnx"

# Pinned identity of the bundled asset; see asr/assets/silero_vad.README.md.
SILERO_VAD_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"

_checked = False
_verified_path: Path | None = None
_keep_context: AbstractContextManager[Path] | None = None


def bundled_silero_vad() -> Path | None:
    """Return the bundled VAD path after SHA-256 verification, or ``None``.

    The check runs once per process and its outcome is cached: the asset is
    immutable inside an installed package, and registration runs on every
    worker-loop iteration. ``None`` means the asset is missing, unreadable, or
    fails its pinned hash — callers must treat that as "sherpa tiers not
    ready", never as a reason to fail.
    """
    global _checked, _verified_path, _keep_context
    if _checked:
        return _verified_path
    _checked = True

    try:
        traversable = files(_ASSET_PACKAGE).joinpath(_ASSET_NAME)
        if not traversable.is_file():
            logger.error("ASR_VAD_ASSET_MISSING: bundled silero VAD %s is missing", _ASSET_NAME)
            return None
        if hashlib.sha256(traversable.read_bytes()).hexdigest() != SILERO_VAD_SHA256:
            logger.error(
                "ASR_VAD_ASSET_HASH_MISMATCH: bundled silero VAD failed SHA-256 verification"
            )
            return None

        # ``as_file`` returns the real path for ordinary installs; the context
        # is kept for the process lifetime so the path stays valid (and stays
        # extracted for exotic zip-style deployments).
        context = as_file(traversable)
        path = context.__enter__()
    except Exception as exc:
        # A broken install (assets not packaged, unreadable file) must hold the
        # sherpa tiers back, never take the engine down at registration time.
        logger.error(
            "ASR_VAD_ASSET_UNAVAILABLE: bundled silero VAD could not be loaded (%s)",
            type(exc).__name__,
        )
        _keep_context = None
        _verified_path = None
        return None
    _keep_context = context
    _verified_path = path
    return _verified_path


def reset_vad_verification() -> None:
    """Drop the cached verification result (tests only)."""
    global _checked, _verified_path, _keep_context
    if _keep_context is not None:
        with suppress(Exception):
            _keep_context.__exit__(None, None, None)
    _checked = False
    _verified_path = None
    _keep_context = None
