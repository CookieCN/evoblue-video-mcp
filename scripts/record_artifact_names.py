"""Emit GITHUB_ENV lines for the release workflow's artifact names.

Derived from pyproject via evoblue_video_mcp.versioning - never a literal
(INSTALLER_RELEASE_CONTRACT section 1).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evoblue_video_mcp.versioning import setup_exe_name, win_zip_name  # noqa: E402


def main() -> int:
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    print(f"SETUP_NAME={setup_exe_name(version)}")
    print(f"ZIP_NAME={win_zip_name(version)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
