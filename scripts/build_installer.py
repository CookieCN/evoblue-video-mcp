"""Build the P7 release artifacts (INSTALLER_RELEASE_CONTRACT section 1).

Produces, from the full onedir bundle:
  - EvoBlueVideoMCP-<npmver>-setup.exe   (Inno Setup, user-level)
  - evoblue-video-mcp-<npmver>-win-full.zip  (portable fallback)
  - SHA256SUMS.txt covering both

Usage:
    uv run python scripts/build_installer.py [--variants full] [--skip-frontend]
        [--skip-package]

The version is read from pyproject.toml (the single source) and normalized
through evoblue_video_mcp.versioning; ISCC.exe is discovered via
$INNO_SETUP_ROOT, the default install path, then PATH.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evoblue_video_mcp.versioning import npm_version, setup_exe_name, win_zip_name  # noqa: E402

_ISCC_CANDIDATES = (
    # machine-wide (default) then per-user (non-elevated installer) locations
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))  # noqa: SIM112
    / "Inno Setup 6"
    / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
)


def read_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def find_iscc() -> Path:
    root = os.environ.get("INNO_SETUP_ROOT")
    candidates = [Path(root) / "ISCC.exe"] if root else []
    candidates.extend(_ISCC_CANDIDATES)
    which = shutil.which("iscc")
    if which:
        candidates.append(Path(which))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "Inno Setup 6 not found; install it (winget install JRSoftware.InnoSetup) "
        "or set INNO_SETUP_ROOT"
    )


def build_setup(iscc: Path, version: str, bundle: Path) -> Path:
    out_name = setup_exe_name(version)
    subprocess.run(
        [
            str(iscc),
            f"/DAppVersion={version}",
            f"/DSourceDir={bundle}",
            f"/O{ROOT / 'dist'}",
            f"/F{Path(out_name).stem}",
            str(ROOT / "packaging" / "installer.iss"),
        ],
        cwd=ROOT,
        check=True,
    )
    out = ROOT / "dist" / out_name
    if not out.is_file():
        raise SystemExit(f"expected installer missing: {out}")
    return out


def build_zip(version: str, bundle: Path) -> Path:
    out = ROOT / "dist" / win_zip_name(version)
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle.parent))
    return out


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(artifacts: list[Path]) -> Path:
    lines = [f"{sha256_of(a)}  {a.name}" for a in artifacts]
    out = ROOT / "dist" / "SHA256SUMS.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", default="full")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument(
        "--skip-package",
        action="store_true",
        help="reuse the existing dist/evoblue-video-mcp-full bundle",
    )
    args = parser.parse_args(argv)

    version = read_version()
    print(f"version: {version} (npm form {npm_version(version)})")

    if not args.skip_package:
        from build_package import build_frontend, build_variant

        if not args.skip_frontend:
            build_frontend()
        for variant in [v.strip() for v in args.variants.split(",") if v.strip()]:
            print(f"=== building bundle variant: {variant}")
            build_variant(variant)

    bundle = ROOT / "dist" / "evoblue-video-mcp-full"
    if not bundle.is_dir():
        raise SystemExit(f"bundle not found: {bundle} (run build_package.py first)")

    iscc = find_iscc()
    print(f"ISCC: {iscc}")
    setup_exe = build_setup(iscc, version, bundle)
    zip_path = build_zip(version, bundle)
    sums = write_checksums([setup_exe, zip_path])

    print("=== artifacts:")
    for artifact in (setup_exe, zip_path, sums):
        print(f"  {artifact.name}: {artifact.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
