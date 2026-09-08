"""P7-a version-consistency gate: one version, no third literals.

The PEP 440 version lives in pyproject.toml and
src/evoblue_video_mcp/__init__.py; the npm form lives in
frontend/package.json (its lockfile root follows automatically). Anything
that derives a display string (installer AppVersion, release tag checks,
build scripts) must derive it from pyproject, never from a third literal
(INSTALLER_RELEASE_CONTRACT section 1).
"""

import json
import re
import tomllib
from pathlib import Path

import evoblue_video_mcp

_ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _package_json_version() -> str:
    data = json.loads(
        (_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    return data["version"]


def _normalize_npm(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)-beta\.(\d+)", version)
    assert match is not None, f"unexpected npm version form: {version}"
    major, minor, patch, beta = match.groups()
    return f"{major}.{minor}.{patch}b{beta}"


def test_pyproject_matches_package_version() -> None:
    assert evoblue_video_mcp.__version__ == _pyproject_version()


def test_package_json_matches_pep440_form() -> None:
    assert _normalize_npm(_package_json_version()) == _pyproject_version()
