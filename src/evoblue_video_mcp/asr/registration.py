"""Expected-state registration of concrete ASR providers into the registry.

This is the one module outside ``asr/providers/`` allowed to name concrete
engines. :func:`register_available_asr_providers` is an idempotent reconcile of
the registry against the current disk and settings state:

- a provider whose inputs are usable (model files present; for whisper.cpp also
  a configured CLI) is registered — replacing an earlier registration whose
  inputs changed, e.g. after the CLI path was edited, or that external code
  overwrote;
- a provider this module registered whose inputs vanished (uninstalled files,
  cleared CLI) is unregistered — and only that provider: ownership is tracked
  per concrete instance, so an external registration under the same id is
  never removed here;
- registrations made elsewhere (user code, tests) are otherwise untouched.

Providers load their models at construction, so an unchanged registration is
kept as-is and only changed inputs trigger a reload. A failing load (corrupt
weights, incompatible engine) is isolated: the tier stays unregistered and a
known-bad marker skips expensive rebuild attempts until an event-driven retry
(``retry_failed_loads=True`` on the install/settings paths) or the signature
changes. Registration runs before the app lifespan, so one bad model must
never take the whole engine down. The sherpa-onnx tiers additionally require
the bundled Silero VAD asset to verify against its pinned SHA-256 (see
:mod:`evoblue_video_mcp.asr.vad`); waiting-job reconciliation shares these
gates — a provider that is not registered is simply "not ready".

Called from engine bootstrap, from :mod:`evoblue_video_mcp.asr.service` before
waiting-job reconciliation, and by the handler factory before each worker
iteration.
"""

import importlib.util
import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path

from evoblue_video_mcp.asr.base import ASRProvider
from evoblue_video_mcp.asr.registry import (
    get_provider,
    register_provider,
    unregister_provider,
)
from evoblue_video_mcp.asr.vad import bundled_silero_vad

logger = logging.getLogger(__name__)

# Inputs this module registered, keyed by provider_id: the exact input
# signature plus the concrete instance it produced (``None`` marks a signature
# whose build failed — a known-bad marker that avoids retrying a heavy load on
# every worker-loop iteration).
_MANAGED: dict[str, tuple[str, ASRProvider | None]] = {}

_WHISPER_PROVIDER_ID = "whisper-cpp-base"
_STANDARD_PROVIDER_ID = "sherpa-onnx-standard"
_LITE_PROVIDER_ID = "sherpa-onnx-lite"
_QWEN3_PROVIDER_ID = "sherpa-onnx-qwen3"

_WHISPER_MODEL_SUBPATH = Path("whisper-cpp-base") / "80da2d8" / "ggml-base.bin"
_STANDARD_SUBDIR = Path("sensevoice-small-int8") / "2024-07-17"
_LITE_SUBDIR = Path("zipformer-ctc-small-zh-int8") / "2025-07-16"
_QWEN3_SUBDIR = Path("qwen3-asr-0.6b-int8") / "2026-03-25"


def register_available_asr_providers(
    model_dir: Path | None,
    whisper_cpp_executable: str | None = None,
    *,
    retry_failed_loads: bool = False,
) -> None:
    """Reconcile built-in provider registrations with disk and settings state.

    ``retry_failed_loads`` drops known-bad markers so a failed load is
    attempted again — used by event paths (a completed install, a settings
    save) where the reason for the failure may have been fixed. The high-
    frequency worker-loop calls keep it off so a corrupt model is not reloaded
    on every idle iteration.
    """
    if retry_failed_loads:
        for known_bad in [pid for pid, (_, owned) in _MANAGED.items() if owned is None]:
            del _MANAGED[known_bad]
    _reconcile_whisper(model_dir, whisper_cpp_executable)
    _reconcile_sherpa(model_dir)


def unregister_managed_provider(provider_id: str) -> None:
    """Unregister a provider only if this module registered it (and it is still ours)."""
    _forget_managed(provider_id)


def reset_managed_registrations() -> None:
    """Forget which providers this module registered (tests only)."""
    _MANAGED.clear()


def _forget_managed(provider_id: str) -> None:
    entry = _MANAGED.pop(provider_id, None)
    if entry is None:
        return
    owned = entry[1]
    if owned is not None and get_provider(provider_id) is owned:
        unregister_provider(provider_id)
    # Otherwise the registry entry is not ours anymore (external registration
    # or a known-bad marker): leave whatever is registered alone.


def _reconcile_managed(
    provider_id: str, signature: str | None, build: Callable[[], ASRProvider] | None
) -> None:
    """Align one managed registration with the desired state.

    ``signature`` is ``None`` when the provider's inputs are not usable right
    now (``build`` is then ignored). A changed signature — or a registry entry
    that is no longer the instance we built — rebuilds the provider, replacing
    whatever is registered, because a valid on-disk configuration is the
    source of truth. A vanished signature unregisters only what this module
    itself registered.
    """
    if signature is None:
        _forget_managed(provider_id)
        return
    assert build is not None

    entry = _MANAGED.get(provider_id)
    if entry is not None and entry[0] == signature:
        owned = entry[1]
        if owned is None:
            return  # known-bad for this exact configuration; retry only via event
        if get_provider(provider_id) is owned:
            return  # up to date and still ours
    # Signature changed, nothing registered yet, or something else took the id:
    # rebuild from the current inputs.
    try:
        provider = build()
    except Exception as exc:
        # A corrupt model or incompatible engine must not take the engine down
        # (registration runs before the app lifespan). The tier simply stays
        # unregistered; its waiting jobs keep waiting until the inputs change
        # or an event path retries. Logs stay sanitized: engine errors embed
        # local paths and usernames, so only the type is recorded.
        logger.error(
            "ASR_PROVIDER_LOAD_FAILED: ASR provider %r failed to load (%s); "
            "it stays unregistered",
            provider_id,
            type(exc).__name__,
        )
        _MANAGED[provider_id] = (signature, None)
        return
    register_provider(provider)
    _MANAGED[provider_id] = (signature, provider)


def _reconcile_whisper(model_dir: Path | None, whisper_cpp_executable: str | None) -> None:
    signature: str | None = None
    build: Callable[[], ASRProvider] | None = None
    if model_dir is not None and whisper_cpp_executable:
        model_file = Path(model_dir) / _WHISPER_MODEL_SUBPATH
        if Path(whisper_cpp_executable).is_file() and model_file.is_file():
            signature = f"cli={whisper_cpp_executable}|model={model_file}"
            build = partial(_build_whisper, whisper_cpp_executable, model_file)
    _reconcile_managed(_WHISPER_PROVIDER_ID, signature, build)


def _build_whisper(executable: str, model_path: Path) -> ASRProvider:
    from evoblue_video_mcp.asr.providers.whisper_cpp import WhisperCppProvider

    return WhisperCppProvider(executable, model_path)


def _sherpa_importable() -> bool:
    return importlib.util.find_spec("sherpa_onnx") is not None


def _reconcile_sherpa(model_dir: Path | None) -> None:
    # Fail closed on the shared VAD first: without a verified asset no sherpa
    # tier can transcribe, whatever the recognition files say.
    vad_model = bundled_silero_vad()
    if (
        model_dir is None
        or not Path(model_dir).is_dir()
        or vad_model is None
        or not _sherpa_importable()
    ):
        _reconcile_managed(_STANDARD_PROVIDER_ID, None, None)
        _reconcile_managed(_LITE_PROVIDER_ID, None, None)
        _reconcile_managed(_QWEN3_PROVIDER_ID, None, None)
        return

    vad = str(vad_model)
    standard_dir = Path(model_dir) / _STANDARD_SUBDIR
    standard_sig = _tier_signature(standard_dir, vad)
    if standard_sig is not None:
        _reconcile_managed(
            _STANDARD_PROVIDER_ID,
            standard_sig,
            lambda: _build_sherpa(
                provider_id=_STANDARD_PROVIDER_ID,
                family="sense_voice",
                model_id="sensevoice-small-int8",
                model_version="2024-07-17",
                tier_dir=standard_dir,
                languages=frozenset({"zh", "en", "ja", "ko", "yue"}),
                vad=vad,
            ),
        )
    else:
        _reconcile_managed(_STANDARD_PROVIDER_ID, None, None)

    lite_dir = Path(model_dir) / _LITE_SUBDIR
    lite_sig = _tier_signature(lite_dir, vad)
    if lite_sig is not None:
        _reconcile_managed(
            _LITE_PROVIDER_ID,
            lite_sig,
            lambda: _build_sherpa(
                provider_id=_LITE_PROVIDER_ID,
                family="zipformer_ctc",
                model_id="zipformer-ctc-small-zh-int8",
                model_version="2025-07-16",
                tier_dir=lite_dir,
                languages=frozenset({"zh"}),
                vad=vad,
            ),
        )
    else:
        _reconcile_managed(_LITE_PROVIDER_ID, None, None)

    qwen3_dir = Path(model_dir) / _QWEN3_SUBDIR
    qwen3_sig = _qwen3_signature(qwen3_dir, vad)
    if qwen3_sig is not None:
        _reconcile_managed(
            _QWEN3_PROVIDER_ID,
            qwen3_sig,
            lambda: _build_sherpa(
                provider_id=_QWEN3_PROVIDER_ID,
                family="qwen3_asr",
                model_id="qwen3-asr-0.6b-int8",
                model_version="2026-03-25",
                tier_dir=qwen3_dir,
                languages=frozenset(
                    {
                        "zh",
                        "en",
                        "yue",
                        "ar",
                        "de",
                        "fr",
                        "es",
                        "pt",
                        "id",
                        "it",
                        "ko",
                        "ru",
                        "th",
                        "vi",
                        "ja",
                        "tr",
                        "hi",
                        "ms",
                        "nl",
                        "sv",
                        "da",
                        "fi",
                        "pl",
                        "cs",
                        "fil",
                        "fa",
                        "el",
                        "hu",
                        "mk",
                        "ro",
                    }
                ),
                vad=vad,
            ),
        )
    else:
        _reconcile_managed(_QWEN3_PROVIDER_ID, None, None)


def _tier_signature(tier_dir: Path, vad: str) -> str | None:
    if (tier_dir / "model.int8.onnx").is_file() and (tier_dir / "tokens.txt").is_file():
        return f"dir={tier_dir}|vad={vad}"
    return None


def _qwen3_signature(tier_dir: Path, vad: str) -> str | None:
    required = (
        "conv_frontend.onnx",
        "encoder.int8.onnx",
        "decoder.int8.onnx",
        "tokenizer/vocab.json",
        "tokenizer/merges.txt",
        "tokenizer/tokenizer_config.json",
    )
    if all((tier_dir / relative).is_file() for relative in required):
        return f"dir={tier_dir}|vad={vad}"
    return None


def _build_sherpa(
    *,
    provider_id: str,
    family: str,
    model_id: str,
    model_version: str,
    tier_dir: Path,
    languages: frozenset[str],
    vad: str,
) -> ASRProvider:
    from evoblue_video_mcp.asr.providers.sherpa_onnx import SherpaModelSpec, SherpaOnnxProvider

    return SherpaOnnxProvider(
        SherpaModelSpec(
            provider_id=provider_id,
            family=family,
            model_id=model_id,
            model_version=model_version,
            model_path=str(tier_dir / "model.int8.onnx"),
            tokens_path=str(tier_dir / "tokens.txt"),
            languages=languages,
            conv_frontend_path=(
                str(tier_dir / "conv_frontend.onnx") if family == "qwen3_asr" else ""
            ),
            encoder_path=(
                str(tier_dir / "encoder.int8.onnx") if family == "qwen3_asr" else ""
            ),
            decoder_path=(
                str(tier_dir / "decoder.int8.onnx") if family == "qwen3_asr" else ""
            ),
            tokenizer_path=str(tier_dir / "tokenizer") if family == "qwen3_asr" else "",
        ),
        vad_model_path=vad,
    )
