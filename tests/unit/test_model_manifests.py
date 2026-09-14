"""Built-in production manifests validate and are releasable against the catalog."""

from evoblue_video_mcp.asr.manifest import is_releasable
from evoblue_video_mcp.asr.manifests import BUILTIN_MANIFESTS, get_builtin_manifest


def test_builtin_manifests_are_releasable_with_only_approved_sources() -> None:
    ids = {manifest.model_id for manifest in BUILTIN_MANIFESTS}
    assert ids == {
        "sensevoice-small-int8",
        "qwen3-asr-0.6b-int8",
        "whisper-cpp-base",
        "zipformer-ctc-small-zh-int8",
    }
    for manifest in BUILTIN_MANIFESTS:
        assert is_releasable(manifest) is True
        assert manifest.redistribution in {"upstream_only", "mirror_approved"}
        if manifest.redistribution == "upstream_only":
            assert all(source.kind == "upstream" for source in manifest.sources)


def test_get_builtin_manifest() -> None:
    standard = get_builtin_manifest("sensevoice-small-int8")
    assert standard is not None
    assert standard.version == "2024-07-17"
    assert standard.languages == ("zh", "en", "ja", "ko", "yue")

    lite = get_builtin_manifest("zipformer-ctc-small-zh-int8")
    assert lite is not None
    assert lite.version == "2025-07-16"
    assert lite.languages == ("zh",)

    whisper = get_builtin_manifest("whisper-cpp-base")
    assert whisper is not None
    assert whisper.version == "80da2d8"
    assert whisper.languages == ("*",)
    assert whisper.archive_format == "raw"

    qwen = get_builtin_manifest("qwen3-asr-0.6b-int8")
    assert qwen is not None
    assert qwen.version == "2026-03-25"
    assert qwen.archive_format == "file-set"
    assert qwen.sources[0].kind == "china-primary"
    assert qwen.sources[0].url.startswith("https://modelscope.cn/")
    assert "/resolve/master" not in qwen.sources[0].url

    assert get_builtin_manifest("unknown") is None


def test_manifest_digests_are_well_formed_and_consistent() -> None:
    for manifest in BUILTIN_MANIFESTS:
        assert sum(f.size_bytes for f in manifest.files) == manifest.installed_size_bytes
        assert all(len(f.sha256) == 64 for f in manifest.files)
        assert all(len(s.sha256) == 64 for s in manifest.sources)
        assert len({s.sha256 for s in manifest.sources}) == 1


def test_archive_whitelists_cover_every_pinned_archive_member() -> None:
    """F2 (feedback #7): the extractor rejects undeclared members, and the
    first real install died on README.md — the whitelist must mirror the
    FULL file list of the pinned archive (verified 2026-09-10 against the
    archives whose sha256 is pinned in the manifests)."""
    standard = get_builtin_manifest("sensevoice-small-int8")
    assert standard is not None
    assert {f.name for f in standard.files} == {
        "LICENSE",
        "README.md",
        "export-onnx.py",
        "model.int8.onnx",
        "tokens.txt",
        "test_wavs/en.wav",
        "test_wavs/ja.wav",
        "test_wavs/ko.wav",
        "test_wavs/yue.wav",
        "test_wavs/zh.wav",
    }
    lite = get_builtin_manifest("zipformer-ctc-small-zh-int8")
    assert lite is not None
    assert {f.name for f in lite.files} == {
        "bbpe.model",
        "model.int8.onnx",
        "tokens.txt",
        "test_wavs/0.wav",
        "test_wavs/1.wav",
        "test_wavs/8k.wav",
    }
