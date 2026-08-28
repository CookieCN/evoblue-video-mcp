"""Expected-state registration: replace on change, unregister on vanish.

The whisper.cpp tier is used throughout because registering it only constructs
a path-holding object (no engine dependency). Sherpa tiers are exercised at the
gate level here; their full registration+transcription chain is covered by the
real-engine tests.
"""

import importlib.util
import logging

import pytest

from evoblue_video_mcp.asr import vad as vad_module
from evoblue_video_mcp.asr.fake import FakeASRProvider
from evoblue_video_mcp.asr.providers.whisper_cpp import WhisperCppProvider
from evoblue_video_mcp.asr.registration import (
    register_available_asr_providers,
    reset_managed_registrations,
)
from evoblue_video_mcp.asr.registry import clear, get_provider, register_provider
from evoblue_video_mcp.asr.vad import bundled_silero_vad, reset_vad_verification


@pytest.fixture(autouse=True)
def _clean_registration_state() -> None:
    clear()
    reset_managed_registrations()
    reset_vad_verification()


def _layout(base, cli_name: str = "whisper-cli.exe"):
    """``base`` is the models_dir; the CLI lives outside it, as a user path would."""
    base.mkdir(parents=True, exist_ok=True)
    cli = base.parent / f"{base.name}-{cli_name}"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_bytes(b"stub cli")
    model_dir = base / "whisper-cpp-base" / "80da2d8"
    model_dir.mkdir(parents=True, exist_ok=True)
    model = model_dir / "ggml-base.bin"
    model.write_bytes(b"ggml")
    return base, cli, model


def test_bundled_vad_verifies_against_pinned_sha() -> None:
    # Guards against the asset silently drifting from the pinned constant.
    assert bundled_silero_vad() is not None


def test_whisper_registers_when_cli_and_model_ready(tmp_path) -> None:
    models, cli, _ = _layout(tmp_path)
    register_available_asr_providers(models, str(cli))
    assert isinstance(get_provider("whisper-cpp-base"), WhisperCppProvider)


def test_whisper_cli_change_replaces_provider(tmp_path) -> None:
    models, cli, _ = _layout(tmp_path)
    register_available_asr_providers(models, str(cli))
    first = get_provider("whisper-cpp-base")

    _, new_cli, _ = _layout(tmp_path / "elsewhere", cli_name="other-whisper-cli.exe")
    register_available_asr_providers(models, str(new_cli))
    second = get_provider("whisper-cpp-base")

    # The registry must not keep pointing at the removed CLI path.
    assert second is not None and first is not None
    assert second is not first
    assert isinstance(second, WhisperCppProvider)


def test_whisper_cli_cleared_unregisters_managed_provider(tmp_path) -> None:
    models, cli, _ = _layout(tmp_path)
    register_available_asr_providers(models, str(cli))
    assert get_provider("whisper-cpp-base") is not None

    register_available_asr_providers(models, None)
    assert get_provider("whisper-cpp-base") is None


def test_whisper_model_file_vanish_unregisters_managed_provider(tmp_path) -> None:
    models, cli, model = _layout(tmp_path)
    register_available_asr_providers(models, str(cli))
    assert get_provider("whisper-cpp-base") is not None

    model.unlink()
    register_available_asr_providers(models, str(cli))
    assert get_provider("whisper-cpp-base") is None


def test_external_registration_survives_when_inputs_absent(tmp_path) -> None:
    external = FakeASRProvider(model_id="whisper-cpp-base", model_version="80da2d8")
    external.provider_id = "whisper-cpp-base"
    register_provider(external)

    register_available_asr_providers(tmp_path / "models", None)
    assert get_provider("whisper-cpp-base") is external


def test_external_overwrite_survives_input_vanish(tmp_path) -> None:
    models, cli, model = _layout(tmp_path)
    register_available_asr_providers(models, str(cli))

    # External code takes over the id after our registration.
    external = FakeASRProvider(model_id="whisper-cpp-base", model_version="80da2d8")
    external.provider_id = "whisper-cpp-base"
    register_provider(external)

    # Inputs vanish: only the instance this module registered may be removed.
    model.unlink()
    register_available_asr_providers(models, str(cli))
    assert get_provider("whisper-cpp-base") is external


def test_external_overwrite_rebuilt_while_inputs_still_valid(tmp_path) -> None:
    models, cli, _ = _layout(tmp_path)
    register_available_asr_providers(models, str(cli))

    external = FakeASRProvider(model_id="whisper-cpp-base", model_version="80da2d8")
    external.provider_id = "whisper-cpp-base"
    register_provider(external)

    # Inputs are still valid: the reconcile re-takes the id with a real build,
    # because a valid on-disk configuration is the source of truth.
    register_available_asr_providers(models, str(cli))
    assert isinstance(get_provider("whisper-cpp-base"), WhisperCppProvider)


@pytest.mark.skipif(
    importlib.util.find_spec("sherpa_onnx") is None, reason="sherpa-onnx not installed"
)
def test_failing_provider_build_is_isolated_and_not_retried(tmp_path, monkeypatch) -> None:
    tier = tmp_path / "models" / "sensevoice-small-int8" / "2024-07-17"
    tier.mkdir(parents=True)
    (tier / "model.int8.onnx").write_bytes(b"weights")
    (tier / "tokens.txt").write_bytes(b"tokens")

    calls = {"n": 0}

    def boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("corrupt weights")

    monkeypatch.setattr("evoblue_video_mcp.asr.registration._build_sherpa", boom)
    models = tmp_path / "models"

    # A failing model load must not raise out of registration.
    register_available_asr_providers(models)
    assert get_provider("sherpa-onnx-standard") is None

    # The high-frequency worker-loop reconcile must not retry the heavy load.
    register_available_asr_providers(models)
    assert calls["n"] == 1

    # Event paths (install completed, settings saved) explicitly retry.
    register_available_asr_providers(models, retry_failed_loads=True)
    assert calls["n"] == 2


@pytest.mark.skipif(
    importlib.util.find_spec("sherpa_onnx") is None, reason="sherpa-onnx not installed"
)
def test_provider_load_failure_logs_stay_sanitized(tmp_path, monkeypatch, caplog) -> None:
    # Engine errors embed local paths and usernames: the log must carry the
    # provider id, a stable code, and the exception type only.
    tier = tmp_path / "models" / "sensevoice-small-int8" / "2024-07-17"
    tier.mkdir(parents=True)
    (tier / "model.int8.onnx").write_bytes(b"weights")
    (tier / "tokens.txt").write_bytes(b"tokens")

    secret = "C:\\Users\\secret-name\\model.int8.onnx truncated"

    def boom(**kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr("evoblue_video_mcp.asr.registration._build_sherpa", boom)

    with caplog.at_level(logging.ERROR, logger="evoblue_video_mcp.asr.registration"):
        register_available_asr_providers(tmp_path / "models")

    text = " ".join(record.getMessage() for record in caplog.records)
    assert "ASR_PROVIDER_LOAD_FAILED" in text
    assert "sherpa-onnx-standard" in text
    assert "RuntimeError" in text
    assert "secret-name" not in text and "truncated" not in text
    assert all(record.exc_info is None and record.exc_text is None for record in caplog.records)


def test_sherpa_refuses_registration_when_bundled_vad_fails_verification(
    tmp_path, monkeypatch
) -> None:
    tier = tmp_path / "models" / "sensevoice-small-int8" / "2024-07-17"
    tier.mkdir(parents=True)
    (tier / "model.int8.onnx").write_bytes(b"weights")
    (tier / "tokens.txt").write_bytes(b"tokens")

    monkeypatch.setattr(vad_module, "SILERO_VAD_SHA256", "0" * 64)
    reset_vad_verification()

    # Fail closed: recognition files alone must not produce a registration.
    register_available_asr_providers(tmp_path / "models")
    assert get_provider("sherpa-onnx-standard") is None
    assert get_provider("sherpa-onnx-lite") is None


@pytest.mark.skipif(
    importlib.util.find_spec("sherpa_onnx") is None, reason="sherpa-onnx not installed"
)
def test_sherpa_registers_when_vad_ok_and_files_present(tmp_path, monkeypatch) -> None:
    tier = tmp_path / "models" / "sensevoice-small-int8" / "2024-07-17"
    tier.mkdir(parents=True)
    (tier / "model.int8.onnx").write_bytes(b"weights")
    (tier / "tokens.txt").write_bytes(b"tokens")

    # The recognition model bytes here are a stub, so force the gate to pass
    # while stubbing construction — a real sherpa load of dummy weights is
    # covered by the real-engine suite, not here.
    assert bundled_silero_vad() is not None
    built = FakeASRProvider(model_id="sensevoice-small-int8")
    built.provider_id = "sherpa-onnx-standard"
    monkeypatch.setattr(
        "evoblue_video_mcp.asr.registration._build_sherpa", lambda **kwargs: built
    )

    register_available_asr_providers(tmp_path / "models")
    assert get_provider("sherpa-onnx-standard") is built
