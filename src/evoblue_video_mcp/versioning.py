"""Single source for release naming (INSTALLER_RELEASE_CONTRACT §1).

pyproject's PEP 440 version is the only literal; every display string —
installer name, zip name, release tag — derives from it here. The build
scripts and the release workflow call into this module instead of keeping
their own copies.
"""

import re

_NPM_FORM = re.compile(r"^(\d+)\.(\d+)\.(\d+)b(\d+)$")


def npm_version(pep440: str) -> str:
    """``0.9.0b1`` -> ``0.9.0-beta.1`` (the npm/display form of the version)."""
    match = _NPM_FORM.match(pep440)
    if match is None:
        # Versions without a beta segment pass through unchanged.
        if re.match(r"^\d+\.\d+\.\d+$", pep440):
            return pep440
        raise ValueError(f"unsupported version form: {pep440!r}")
    major, minor, patch, beta = match.groups()
    return f"{major}.{minor}.{patch}-beta.{beta}"


def tag_name(pep440: str) -> str:
    """Git release tag for a pyproject version, e.g. ``v0.9.0-beta.1``."""
    return f"v{npm_version(pep440)}"


def setup_exe_name(pep440: str) -> str:
    return f"EvoBlueVideoMCP-{npm_version(pep440)}-setup.exe"


def win_zip_name(pep440: str) -> str:
    return f"evoblue-video-mcp-{npm_version(pep440)}-win-full.zip"
