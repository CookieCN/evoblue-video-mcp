"""Measure sherpa-onnx int8 artifacts and emit pinned manifest/catalog values.

Dev-only. Downloads the int8-only archives from GitHub Releases, records the
archive SHA-256 and size, extracts the kept files (flattened to basename, matching
``fetch_model._extract_keep`` and ``install_archive``), and hashes each. The output
is the exact material to paste into ``asr/catalog.py`` and ``asr/manifests.py``.

Run:  uv run python scripts/measure_manifest.py --work-dir <dir> [--tier all|lite|standard]
"""

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path

BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"

MODELS = {
    "lite": {
        "model_id": "zipformer-ctc-small-zh-int8",
        "version": "2025-07-16",
        "archive": "sherpa-onnx-zipformer-ctc-small-zh-int8-2025-07-16.tar.bz2",
        "keep": {"model.int8.onnx", "tokens.txt", "bbpe.model"},
    },
    "standard": {
        "model_id": "sensevoice-small-int8",
        "version": "2024-07-17",
        "archive": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        "keep": {"model.int8.onnx", "tokens.txt"},
    },
}

_CHUNK = 1 << 20


def _sha_file(path: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            size += len(chunk)
            hasher.update(chunk)
    return size, hasher.hexdigest()


def _download(url: str, dest: Path) -> tuple[int, str]:
    import urllib.request

    print(f"downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            out.write(chunk)
    return _sha_file(dest)


def _extract_keep(archive: Path, target: Path, keep: set[str]) -> dict[str, tuple[int, str]]:
    target.mkdir(parents=True, exist_ok=True)
    result: dict[str, tuple[int, str]] = {}
    with tarfile.open(archive, "r:bz2") as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if name in keep:
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                (target / name).write_bytes(extracted.read())
                result[name] = _sha_file(target / name)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure int8 model artifact metadata")
    parser.add_argument("--work-dir", required=True, help="directory to download and extract into")
    parser.add_argument("--tier", choices=["lite", "standard", "all"], default="all")
    args = parser.parse_args(argv)

    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)

    tiers = ["lite", "standard"] if args.tier == "all" else [args.tier]
    report: dict[str, dict] = {}
    for tier in tiers:
        spec = MODELS[tier]
        archive_path = work / spec["archive"]
        if archive_path.exists():
            archive_size, archive_sha = _sha_file(archive_path)
            print(f"reusing cached archive {archive_path}", file=sys.stderr)
        else:
            archive_size, archive_sha = _download(f"{BASE_URL}/{spec['archive']}", archive_path)

        extracted = work / f"{spec['model_id']}-{spec['version']}"
        files = _extract_keep(archive_path, extracted, spec["keep"])

        report[tier] = {
            "model_id": spec["model_id"],
            "version": spec["version"],
            "archive": spec["archive"],
            "compressed_size_bytes": archive_size,
            "archive_sha256": archive_sha,
            "installed_size_bytes": sum(size for size, _ in files.values()),
            "files": [
                {"name": name, "size_bytes": size, "sha256": sha}
                for name, (size, sha) in sorted(files.items())
            ],
        }

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
