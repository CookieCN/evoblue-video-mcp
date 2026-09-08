"""Print the pyproject version (one line) for shell consumers."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
