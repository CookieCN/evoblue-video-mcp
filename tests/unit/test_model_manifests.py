"""Built-in production manifests validate and are releasable against the catalog."""

from evoblue_video_mcp.asr.manifest import is_releasable
from evoblue_video_mcp.asr.manifests import BUILTIN_MANIFESTS, get_builtin_manifest


def test_builtin_manifests_are_releasable_and_upstream_only() -> None:
    ids = {manifest.model_id for manifest in BUILTIN_MANIFESTS}
    assert ids == {
        "sensevoice-small-int8",
        "whisper-cpp-base",
        "zipformer-ctc-small-zh-int8",
    }
    for manifest in BUILTIN_MANIFESTS:
        assert is_releasable(manifest) is True
        assert manifest.redistribution == "upstream_only"
        # No mirror/cdn may ship yet: only the pinned original publisher URL.
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

    assert get_builtin_manifest("unknown") is None


def test_manifest_digests_are_well_formed_and_consistent() -> None:
    for manifest in BUILTIN_MANIFESTS:
        assert sum(f.size_bytes for f in manifest.files) == manifest.installed_size_bytes
        assert all(len(f.sha256) == 64 for f in manifest.files)
        assert all(len(s.sha256) == 64 for s in manifest.sources)
        assert len({s.sha256 for s in manifest.sources}) == 1
