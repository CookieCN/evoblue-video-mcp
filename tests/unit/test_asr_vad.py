"""Bundled-VAD fault boundary: broken installs degrade, never raise.

Registration runs before the app lifespan, so resource discovery, reading,
verification, and materialization must each stay inside the boundary — any
ordinary failure resolves to ``None`` ("sherpa tiers not ready") instead of
blocking engine startup.
"""

import logging
from contextlib import AbstractContextManager
from importlib.resources.abc import Traversable
from pathlib import Path

import pytest

from evoblue_video_mcp.asr import vad as vad_module
from evoblue_video_mcp.asr.registration import register_available_asr_providers
from evoblue_video_mcp.asr.registry import get_provider
from evoblue_video_mcp.asr.vad import bundled_silero_vad, reset_vad_verification


@pytest.fixture(autouse=True)
def _reset_vad_state() -> None:
    reset_vad_verification()
    yield
    reset_vad_verification()


class _BrokenTraversable(Traversable):
    """Pretends the asset exists but blows up when its bytes are read."""

    def __traversable__(self) -> str:  # pragma: no cover - protocol formality
        return "silero_vad.onnx"

    def is_file(self) -> bool:
        return True

    def read_bytes(self) -> bytes:
        raise PermissionError(13, "Access is denied")

    def open(self, *args, **kwargs):  # pragma: no cover - unused path
        raise PermissionError(13, "Access is denied")

    def iterdir(self):  # pragma: no cover - not a directory
        raise NotADirectoryError(20)

    def joinpath(self, *descendants) -> Traversable:
        return self

    def is_dir(self) -> bool:
        return False

    def name(self) -> str:  # pragma: no cover - property form
        return "silero_vad.onnx"


def test_missing_asset_package_resolves_to_none(monkeypatch) -> None:
    def no_package(package: object) -> None:
        raise ModuleNotFoundError("No module named 'evoblue_video_mcp.asr.assets'")

    monkeypatch.setattr(vad_module, "files", no_package)
    assert bundled_silero_vad() is None


def test_unreadable_asset_resolves_to_none(monkeypatch) -> None:
    monkeypatch.setattr(vad_module, "files", lambda package: _BrokenTraversable())
    assert bundled_silero_vad() is None


def test_failed_materialization_resolves_to_none(monkeypatch) -> None:
    class _ExplodingContext(AbstractContextManager[Path]):
        def __enter__(self) -> Path:
            raise OSError("materialization failed")

    monkeypatch.setattr(
        vad_module,
        "as_file",
        lambda traversable: _ExplodingContext(),
    )
    assert bundled_silero_vad() is None


def test_registration_survives_broken_vad_asset(tmp_path, monkeypatch) -> None:
    # The engine path: even with the asset completely unavailable, provider
    # registration completes and simply leaves the sherpa tiers unregistered.
    monkeypatch.setattr(
        vad_module,
        "files",
        lambda package: (_ for _ in ()).throw(ModuleNotFoundError("assets missing")),
    )

    register_available_asr_providers(tmp_path / "models")
    assert get_provider("sherpa-onnx-standard") is None
    assert get_provider("sherpa-onnx-lite") is None


def test_failure_logs_carry_stable_codes_without_detail(monkeypatch, caplog) -> None:
    monkeypatch.setattr(vad_module, "files", lambda package: _BrokenTraversable())

    with caplog.at_level(logging.ERROR, logger=vad_module.logger.name):
        assert bundled_silero_vad() is None

    text = " ".join(record.getMessage() for record in caplog.records)
    assert "ASR_VAD_ASSET_UNAVAILABLE" in text
    assert "PermissionError" in text
    assert "Access is denied" not in text
    assert all(record.exc_info is None and record.exc_text is None for record in caplog.records)
