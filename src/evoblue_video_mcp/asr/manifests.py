"""Built-in production model manifests (Lite + Standard + optional Whisper).

These are the release-grade manifests the Model Manager and WebUI enumerate.
Each pins the measured GitHub release archive and per-file digests (see
``scripts/measure_manifest.py``) and carries ``redistribution="upstream_only"``,
so only the original publisher URL is permitted. A China mirror is deliberately
absent until redistribution is reviewed; see
``docs/ASR_CHINA_SOURCE_QUALIFICATION.md``.
"""

from evoblue_video_mcp.asr.manifest import ModelManifest

_STANDARD_SENSEVOICE = {
    "model_id": "sensevoice-small-int8",
    "version": "2024-07-17",
    "provider": "sherpa-onnx",
    "languages": ["zh", "en", "ja", "ko", "yue"],
    "platforms": ["windows-x86_64"],
    "compressed_size_bytes": 163002883,
    "installed_size_bytes": 239549735,
    "license": "FunASR Model License 1.1",
    "attribution": "FunAudioLLM (Alibaba)",
    "upstream_url": "https://github.com/FunAudioLLM/SenseVoice",
    "redistribution": "upstream_only",
    "archive_format": "tar.bz2",
    "sources": [
        {
            "url": (
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
            ),
            "kind": "upstream",
            "sha256": "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
            "size_bytes": 163002883,
        }
    ],
    "files": [
        {
            "name": "model.int8.onnx",
            "size_bytes": 239233841,
            "sha256": "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        },
        {
            "name": "tokens.txt",
            "size_bytes": 315894,
            "sha256": "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
        },
    ],
}

_LITE_ZIPFORMER = {
    "model_id": "zipformer-ctc-small-zh-int8",
    "version": "2025-07-16",
    "provider": "sherpa-onnx",
    "languages": ["zh"],
    "platforms": ["windows-x86_64"],
    "compressed_size_bytes": 50536402,
    "installed_size_bytes": 62935406,
    "license": "Apache-2.0 (icefall/WenetSpeech upstream; exact archive carries no license file)",
    "attribution": "k2-fsa sherpa-onnx / icefall",
    "upstream_url": "https://github.com/k2-fsa/icefall",
    "redistribution": "upstream_only",
    "archive_format": "tar.bz2",
    "sources": [
        {
            "url": (
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-zipformer-ctc-small-zh-int8-2025-07-16.tar.bz2"
            ),
            "kind": "upstream",
            "sha256": "6a71c0cc442ba85ac1455ca23e4561e2a1ef18e55269a184a649d54e9cd0524c",
            "size_bytes": 50536402,
        }
    ],
    "files": [
        {
            "name": "bbpe.model",
            "size_bytes": 255180,
            "sha256": "503204e0690eff065e30d0e01898c9ab06d0e6dc376a741eb6846198f95b2f82",
        },
        {
            "name": "model.int8.onnx",
            "size_bytes": 62666860,
            "sha256": "32e5f17cc9a77d480f8d94bda97b7cc7a40965b6651b35385813f561a74129c8",
        },
        {
            "name": "tokens.txt",
            "size_bytes": 13366,
            "sha256": "6fed8c6c248516f38e7faa19404b57413e8ce259f1cbc1fa4aebc86eac32fdfd",
        },
    ],
}

_WHISPER_CPP_BASE = {
    "model_id": "whisper-cpp-base",
    "version": "80da2d8",
    "provider": "whisper.cpp",
    "languages": ["*"],
    "platforms": ["windows-x86_64", "macos-x86_64", "macos-arm64", "linux-x86_64"],
    "compressed_size_bytes": 147951465,
    "installed_size_bytes": 147951465,
    "license": "MIT",
    "attribution": "OpenAI Whisper / ggml-org whisper.cpp conversion",
    "upstream_url": "https://github.com/ggml-org/whisper.cpp",
    "redistribution": "upstream_only",
    "archive_format": "raw",
    "sources": [
        {
            "url": (
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
                "80da2d8bfee42b0e836fc3a9890373e5defc00a6/ggml-base.bin"
            ),
            "kind": "upstream",
            "sha256": "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
            "size_bytes": 147951465,
        }
    ],
    "files": [
        {
            "name": "ggml-base.bin",
            "size_bytes": 147951465,
            "sha256": "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        }
    ],
}

BUILTIN_MANIFESTS: tuple[ModelManifest, ...] = tuple(
    ModelManifest.model_validate(data)
    for data in (_LITE_ZIPFORMER, _STANDARD_SENSEVOICE, _WHISPER_CPP_BASE)
)

_MANIFEST_BY_ID: dict[str, ModelManifest] = {
    manifest.model_id: manifest for manifest in BUILTIN_MANIFESTS
}


def get_builtin_manifest(model_id: str) -> ModelManifest | None:
    """Return a validated built-in manifest by model id, or ``None``."""
    return _MANIFEST_BY_ID.get(model_id)
