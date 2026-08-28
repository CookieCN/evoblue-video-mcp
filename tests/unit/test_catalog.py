"""Trusted catalog: approval records and fail-closed semantics."""

from evoblue_video_mcp.asr.catalog import APPROVED_CATALOG
from evoblue_video_mcp.asr.manifest import ModelManifest, is_releasable


def _manifest() -> dict:
    return {
        "model_id": "sensevoice-small-int8",
        "version": "2024-07-17",
        "provider": "sherpa-onnx",
        "languages": ["zh"],
        "platforms": ["windows-x86_64"],
        "compressed_size_bytes": 163_002_883,
        "installed_size_bytes": 239_549_735,
        "license": "FunASR Model License 1.1",
        "attribution": "FunAudioLLM SenseVoice",
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
                "size_bytes": 163_002_883,
            }
        ],
        "files": [
            {"name": "model.int8.onnx", "size_bytes": 239_233_841, "sha256": "b" * 64},
            {"name": "tokens.txt", "size_bytes": 315_894, "sha256": "c" * 64},
        ],
    }


def test_catalog_contains_sensevoice_approval() -> None:
    approved = APPROVED_CATALOG[("sensevoice-small-int8", "2024-07-17")]
    assert approved


def test_approved_entry_is_releasable() -> None:
    manifest = ModelManifest.model_validate(_manifest())
    assert is_releasable(manifest) is True


def test_missing_catalog_entry_fails_closed() -> None:
    data = _manifest()
    data["version"] = "2030-01-01"
    manifest = ModelManifest.model_validate(data)
    assert is_releasable(manifest) is False


def test_lite_zipformer_is_approved_and_releasable() -> None:
    manifest = ModelManifest.model_validate(
        {
            "model_id": "zipformer-ctc-small-zh-int8",
            "version": "2025-07-16",
            "provider": "sherpa-onnx",
            "languages": ["zh"],
            "platforms": ["windows-x86_64"],
            "compressed_size_bytes": 50_536_402,
            "installed_size_bytes": 62_935_406,
            "license": "Apache-2.0 (upstream)",
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
                    "size_bytes": 50_536_402,
                }
            ],
            "files": [
                {
                    "name": "model.int8.onnx",
                    "size_bytes": 62_666_860,
                    "sha256": "32e5f17cc9a77d480f8d94bda97b7cc7a40965b6651b35385813f561a74129c8",
                },
                {"name": "tokens.txt", "size_bytes": 13_366, "sha256": "6" * 64},
                {"name": "bbpe.model", "size_bytes": 255_180, "sha256": "5" * 64},
            ],
        }
    )
    assert is_releasable(manifest) is True
