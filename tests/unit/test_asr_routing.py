"""ASR-3 language/tier routing is deterministic and never downloads implicitly."""

from evoblue_video_mcp.asr.routing import ProviderOption, route_asr

STANDARD = ProviderOption(
    provider_id="sherpa-onnx-standard",
    model_id="sensevoice-small-int8",
    model_version="2024-07-17",
    tier="standard",
    languages=frozenset({"zh", "en", "ja", "ko", "yue"}),
    installed=False,
    formal_default=False,
)
LITE = ProviderOption(
    provider_id="sherpa-onnx-lite",
    model_id="zipformer-ctc-small-zh-int8",
    model_version="2025-07-16",
    tier="lite",
    languages=frozenset({"zh"}),
    installed=False,
    formal_default=False,
)
WHISPER = ProviderOption(
    provider_id="whisper-cpp-base",
    model_id="whisper-cpp-base",
    model_version="80da2d8",
    tier="multilingual",
    languages=frozenset({"*"}),
    installed=False,
    formal_default=False,
)
QWEN = ProviderOption(
    provider_id="sherpa-onnx-qwen3",
    model_id="qwen3-asr-0.6b-int8",
    model_version="2026-03-25",
    tier="qwen3",
    languages=frozenset({"zh", "en", "fr"}),
    installed=False,
    formal_default=False,
)


def test_chinese_missing_model_recommends_standard_not_unlicensed_lite() -> None:
    decision = route_asr("zh-CN", "auto", (LITE, STANDARD, WHISPER))
    assert decision.provider_id == "sherpa-onnx-standard"
    assert decision.model_id == "sensevoice-small-int8"
    assert decision.installed is False


def test_installed_lite_can_complete_chinese_onboarding_alone() -> None:
    decision = route_asr("zh", "auto", (LITE.__class__(**{**LITE.__dict__, "installed": True}),))
    assert decision.provider_id == "sherpa-onnx-lite"
    assert decision.installed is True


def test_sensevoice_language_never_recommends_whisper() -> None:
    decision = route_asr("ja", "auto", (STANDARD, WHISPER))
    assert decision.model_id == "sensevoice-small-int8"


def test_language_outside_sensevoice_recommends_whisper() -> None:
    decision = route_asr("fr", "auto", (STANDARD, QWEN, WHISPER))
    assert decision.provider_id == "whisper-cpp-base"
    assert decision.installed is False


def test_installed_qwen_is_reused_for_its_multilingual_coverage() -> None:
    installed_qwen = QWEN.__class__(**{**QWEN.__dict__, "installed": True})
    decision = route_asr("fr", "auto", (STANDARD, installed_qwen, WHISPER))
    assert decision.provider_id == "sherpa-onnx-qwen3"
    assert decision.installed is True


def test_user_pinned_provider_overrides_automatic_routing() -> None:
    installed_standard = STANDARD.__class__(**{**STANDARD.__dict__, "installed": True})
    decision = route_asr("zh", "sherpa-onnx-lite", (LITE, installed_standard, WHISPER))
    assert decision.provider_id == "sherpa-onnx-lite"
    assert decision.installed is False
