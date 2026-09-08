"""Verify a release tag matches the pyproject version (npm-normalized).

Used by release.yml's first job: the tag ``v0.9.0-beta.1`` must correspond to
pyproject ``0.9.0b1`` through evoblue_video_mcp.versioning.tag_name — never a
raw string comparison, which silently fails across the PEP 440 / npm forms.

Usage:
    uv run python scripts/check_release_tag.py <tag>
"""

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evoblue_video_mcp.versioning import npm_version  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_release_tag.py <tag>", file=sys.stderr)
        return 2
    tag = argv[0]
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    expected = f"v{npm_version(version)}"
    # both spellings of the same release are accepted (npm + PEP 440 raw)
    if tag not in {expected, f"v{version}"}:
        print(
            f"tag {tag!r} does not match pyproject version {version!r} "
            f"(expected tag {expected!r})",
            file=sys.stderr,
        )
        return 1
    print(f"tag {tag} matches pyproject {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
