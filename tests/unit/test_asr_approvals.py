"""Formal default approvals: exact-version keying and recorded gate outcomes."""

from evoblue_video_mcp.asr.approvals import get_approval, is_formal_default


def test_standard_is_the_only_formal_default() -> None:
    assert is_formal_default("sensevoice-small-int8", "2024-07-17")
    assert not is_formal_default("zipformer-ctc-small-zh-int8", "2025-07-16")
    assert not is_formal_default("whisper-cpp-base", "80da2d8")


def test_approval_is_keyed_by_exact_version() -> None:
    # A republished or bumped version starts unapproved: the gate applies to
    # the exact artifact that was benchmarked, never the model id alone.
    assert not is_formal_default("sensevoice-small-int8", "2099-01-01")
    assert get_approval("sensevoice-small-int8", "2099-01-01") is None


def test_unapproved_models_carry_their_reason() -> None:
    lite = get_approval("zipformer-ctc-small-zh-int8", "2025-07-16")
    assert lite is not None
    assert lite.approved is False
    assert "entity recall" in lite.reason
    whisper = get_approval("whisper-cpp-base", "80da2d8")
    assert whisper is not None
    assert whisper.approved is False


def test_unknown_models_have_no_approval() -> None:
    assert get_approval("unknown-model", "v0") is None
    assert not is_formal_default("unknown-model", "v0")
