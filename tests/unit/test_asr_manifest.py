"""Model manifest validation: redistribution, source approval, artifact consistency."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evoblue_video_mcp.asr.manifest import (
    ManifestValidationError,
    ModelFile,
    ModelManifest,
    file_set_fingerprint,
    is_releasable,
    load_manifest,
)

_SENSEVOICE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
)
_SENSEVOICE_SHA = "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e"


def _valid_manifest() -> dict:
    return {
        "model_id": "sensevoice-small-int8",
        "version": "2024-07-17",
        "provider": "sherpa-onnx",
        "languages": ["zh", "en", "ja", "ko", "yue"],
        "platforms": ["windows-x86_64", "linux-x86_64"],
        "compressed_size_bytes": 163_002_883,
        "installed_size_bytes": 239_549_735,
        "license": "FunASR Model License 1.1",
        "attribution": "FunAudioLLM SenseVoice",
        "upstream_url": "https://github.com/FunAudioLLM/SenseVoice",
        "redistribution": "upstream_only",
        "archive_format": "tar.bz2",
        "sources": [
            {
                "url": _SENSEVOICE_URL,
                "kind": "upstream",
                "sha256": _SENSEVOICE_SHA,
                "size_bytes": 163_002_883,
            }
        ],
        "files": [
            {"name": "model.int8.onnx", "size_bytes": 239_233_841, "sha256": "b" * 64},
            {"name": "tokens.txt", "size_bytes": 315_894, "sha256": "c" * 64},
        ],
    }


def test_valid_manifest_is_releasable() -> None:
    manifest = ModelManifest.model_validate(_valid_manifest())
    assert manifest.model_id == "sensevoice-small-int8"
    assert is_releasable(manifest) is True


def test_rejects_non_hex_sha256() -> None:
    data = _valid_manifest()
    data["sources"][0]["sha256"] = "not-a-hash"
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_rejects_unsafe_model_id_path() -> None:
    data = _valid_manifest()
    data["model_id"] = "../evil"
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_rejects_unknown_platform() -> None:
    data = _valid_manifest()
    data["platforms"] = ["windows-x86_64", "templeos"]
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_rejects_empty_sources() -> None:
    data = _valid_manifest()
    data["sources"] = []
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_redistribution_is_required() -> None:
    data = _valid_manifest()
    del data["redistribution"]
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_archive_format_is_required() -> None:
    data = _valid_manifest()
    del data["archive_format"]
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_rejects_http_source() -> None:
    data = _valid_manifest()
    data["sources"][0]["url"] = "http://github.com/example/m.tar.bz2"
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_source_size_must_match_compressed_size() -> None:
    data = _valid_manifest()
    data["sources"][0]["size_bytes"] = 123
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_source_sha_must_be_identical() -> None:
    data = _valid_manifest()
    data["sources"].append(
        {
            "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/other.tar.bz2",
            "kind": "upstream",
            "sha256": "d" * 64,
            "size_bytes": 163_000_000,
        }
    )
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_files_required() -> None:
    data = _valid_manifest()
    data["files"] = []
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_file_total_must_match_installed_size() -> None:
    data = _valid_manifest()
    data["files"][0]["size_bytes"] = 1
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_duplicate_file_names_rejected() -> None:
    data = _valid_manifest()
    data["files"].append(
        {"name": "model.int8.onnx", "size_bytes": 100, "sha256": "e" * 64}
    )
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_file_set_requires_safe_source_paths_and_pinned_fingerprint() -> None:
    files = [
        ModelFile(
            name="model/encoder.int8.onnx",
            source_path="model_0.6B/encoder.int8.onnx",
            size_bytes=3,
            sha256="a" * 64,
        )
    ]
    fingerprint = file_set_fingerprint(tuple(files))
    data = _valid_manifest()
    data.update(
        {
            "compressed_size_bytes": 3,
            "installed_size_bytes": 3,
            "redistribution": "mirror_approved",
            "archive_format": "file-set",
            "sources": [
                {
                    "url": "https://modelscope.cn/models/example/model/resolve/revision",
                    "kind": "china-primary",
                    "sha256": fingerprint,
                    "size_bytes": 3,
                }
            ],
            "files": [file.model_dump() for file in files],
        }
    )
    manifest = ModelManifest.model_validate(data)
    assert manifest.files[0].name == "model/encoder.int8.onnx"

    data["sources"][0]["sha256"] = "b" * 64
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)

    data["sources"][0]["sha256"] = fingerprint
    data["files"][0]["source_path"] = "../encoder.onnx"
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_upstream_only_rejects_mirror_source() -> None:
    data = _valid_manifest()
    data["sources"][0]["kind"] = "china-primary"
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)


def test_unapproved_url_is_not_releasable() -> None:
    data = _valid_manifest()
    data["sources"][0]["url"] = (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/other.tar.bz2"
    )
    manifest = ModelManifest.model_validate(data)
    assert is_releasable(manifest) is False


def test_fail_closed_when_not_in_catalog() -> None:
    data = _valid_manifest()
    data["model_id"] = "unknown-model"
    manifest = ModelManifest.model_validate(data)
    assert is_releasable(manifest) is False


def test_blocked_is_not_releasable() -> None:
    data = _valid_manifest()
    data["redistribution"] = "blocked"
    manifest = ModelManifest.model_validate(data)
    assert is_releasable(manifest) is False


def test_manifest_and_nested_models_are_immutable() -> None:
    manifest = ModelManifest.model_validate(_valid_manifest())
    with pytest.raises(ValidationError):
        manifest.redistribution = "blocked"
    with pytest.raises(ValidationError):
        manifest.sources[0].kind = "cdn"
    with pytest.raises(AttributeError):
        manifest.sources.reverse()
    with pytest.raises(ValidationError):
        manifest.files[0].sha256 = "d" * 64


def test_load_manifest_rejects_invalid_json(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestValidationError):
        load_manifest(path)


def test_load_manifest_round_trips(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_valid_manifest()), encoding="utf-8")
    manifest = load_manifest(path)
    assert manifest.model_id == "sensevoice-small-int8"


def test_example_model_manifest_loads_but_not_releasable() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "asr_benchmark"
        / "model_manifest.example.json"
    )
    manifest = load_manifest(path)
    assert manifest.model_id == "sensevoice-small-int8"
    # example.invalid host is not in the catalog, so the example loads but is not releasable.
    assert is_releasable(manifest) is False
