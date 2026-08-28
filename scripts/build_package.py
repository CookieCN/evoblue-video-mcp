"""Build the packaging matrix: PyInstaller onedir base + full variants.

Usage:
    uv run python scripts/build_package.py [--skip-frontend] [--variants base,full]

For each variant the script runs PyInstaller with the shared spec, copies the
third-party notices into the bundle, and prints the measured onedir sizes and
the ASR-runtime installer delta (full minus base) — the number ASR_PLAN
acceptance requires for every optional provider. Model weights are never
bundled; users install them through the Model Manager.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def build_frontend() -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm not found; build the WebUI or pass --skip-frontend")
    frontend = ROOT / "frontend"
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True)
    dist = frontend / "dist"
    print(f"frontend built: {sum(1 for _ in dist.rglob('*') if _.is_file())} files")


def _tree_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def build_variant(variant: str) -> Path:
    env = dict(os.environ, EVOBLUE_PKG_VARIANT=variant)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "packaging/evoblue_video_mcp.spec"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    bundle = DIST / f"evoblue-video-mcp-{variant}"
    if not bundle.is_dir():
        raise SystemExit(f"expected bundle missing: {bundle}")

    # Ship the WebUI inside the bundle and keep notices next to the executable.
    webui = bundle / "webui"
    frontend_dist = ROOT / "frontend" / "dist"
    if frontend_dist.is_dir() and not webui.exists():
        shutil.copytree(frontend_dist, webui)
    for notice in ("THIRD_PARTY_NOTICES.md", "docs/ASR_MODEL_LICENSES.md"):
        src = ROOT / notice
        if src.is_file():
            shutil.copy2(src, bundle / src.name)
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", default="base,full")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args(argv)

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    if "full" in variants and not args.skip_frontend:
        build_frontend()

    sizes: dict[str, int] = {}
    for variant in variants:
        print(f"=== building variant: {variant}")
        bundle = build_variant(variant)
        sizes[variant] = _tree_size(bundle)
        print(f"=== {variant}: {sizes[variant] / 1e6:.1f} MB -> {bundle}")

    if "base" in sizes and "full" in sizes:
        delta = sizes["full"] - sizes["base"]
        print(f"=== ASR runtime installer delta (full - base): {delta / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
