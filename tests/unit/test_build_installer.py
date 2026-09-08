"""P7-006: installer build-script helpers (version source, ISCC discovery)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_installer  # noqa: E402


def test_read_version_comes_from_pyproject() -> None:
    version = build_installer.read_version()
    assert version
    assert version[0].isdigit()


def test_find_iscc_env_override(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "ISCC.exe"
    fake.write_bytes(b"stub")
    monkeypatch.setenv("INNO_SETUP_ROOT", str(tmp_path))
    assert build_installer.find_iscc() == fake


def test_find_iscc_missing_fails_cleanly(monkeypatch) -> None:
    monkeypatch.delenv("INNO_SETUP_ROOT", raising=False)
    monkeypatch.setattr(
        build_installer, "_ISCC_CANDIDATES", (Path("Z:/definitely/missing/ISCC.exe"),)
    )
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(SystemExit, match="Inno Setup 6 not found"):
        build_installer.find_iscc()
