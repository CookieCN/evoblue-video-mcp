"""Download sherpa-onnx int8-only model artifacts for development (ASR-1).

The production Model Manager (ASR-2) supersedes this helper. It downloads the
int8-only archives, extracts only the ONNX weights and tokens, records measured
download size and SHA-256, and never keeps the FP32 full archive.
"""

import argparse
import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path

BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
VAD_MODEL = "silero_vad.onnx"

MODELS = {
    "lite": {
        "archive": "sherpa-onnx-zipformer-ctc-small-zh-int8-2025-07-16.tar.bz2",
        "dir": "zipformer-ctc-small-zh-int8",
    },
    "standard": {
        "archive": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        "dir": "sensevoice-small-int8",
    },
}

# ``bbpe.model`` is part of the Lite artifact and is cheap to preserve for
# reproducibility, even though sherpa's current Zipformer CTC API decodes from
# model metadata + tokens. Never retain an unused FP32 ``model.onnx``.
_KEEP = {"model.int8.onnx", "tokens.txt", "bbpe.model"}


def _download(url: str, dest: Path) -> tuple[int, str]:
    print(f"downloading {url}")
    with urllib.request.urlopen(url) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    size = dest.stat().st_size
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    print(f"  {size} bytes  sha256={sha}")
    return size, sha


def _extract_keep(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if name in _KEEP:
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                (target / name).write_bytes(extracted.read())
                print(f"  extracted {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch sherpa-onnx int8-only models")
    parser.add_argument("--model-dir", required=True, help="directory to install models into")
    parser.add_argument("--tier", choices=["lite", "standard", "all"], default="all")
    args = parser.parse_args(argv)

    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    tiers = ["lite", "standard"] if args.tier == "all" else [args.tier]
    for tier in tiers:
        spec = MODELS[tier]
        archive = model_dir / spec["archive"]
        _download(f"{BASE_URL}/{spec['archive']}", archive)
        _extract_keep(archive, model_dir / spec["dir"])

    _download(f"{BASE_URL}/{VAD_MODEL}", model_dir / VAD_MODEL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
