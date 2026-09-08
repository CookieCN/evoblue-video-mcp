"""Deterministic ASR tier routing with explicit, non-downloading recommendations."""

from dataclasses import dataclass

SENSEVOICE_LANGUAGES = frozenset({"zh", "en", "ja", "ko", "yue"})
QWEN3_LANGUAGES = frozenset(
    {
        "zh",
        "en",
        "yue",
        "ar",
        "de",
        "fr",
        "es",
        "pt",
        "id",
        "it",
        "ko",
        "ru",
        "th",
        "vi",
        "ja",
        "tr",
        "hi",
        "ms",
        "nl",
        "sv",
        "da",
        "fi",
        "pl",
        "cs",
        "fil",
        "fa",
        "el",
        "hu",
        "mk",
        "ro",
    }
)


@dataclass(frozen=True)
class ProviderOption:
    provider_id: str
    model_id: str
    model_version: str
    tier: str
    languages: frozenset[str]
    installed: bool
    formal_default: bool


@dataclass(frozen=True)
class RoutingDecision:
    provider_id: str
    model_id: str
    model_version: str
    tier: str
    installed: bool
    recommendation: bool
    formal_default: bool


def normalize_language(language: str | None) -> str | None:
    if not language or language.lower() in {"auto", "unknown", "und"}:
        return None
    token = language.strip().lower().replace("_", "-").split("-", 1)[0]
    return {"cmn": "zh", "zho": "zh", "cantonese": "yue"}.get(token, token)


def _supports(option: ProviderOption, language: str | None) -> bool:
    return language is None or "*" in option.languages or language in option.languages


def route_asr(
    language: str | None,
    preference: str,
    options: tuple[ProviderOption, ...],
) -> RoutingDecision:
    """Choose a ready provider or one explicit model recommendation.

    This function has no side effects. In particular, a recommendation never
    starts a model download; the caller persists ``waiting_for_model`` and waits
    for WebUI consent.
    """
    lang = normalize_language(language)
    by_provider = {option.provider_id: option for option in options}

    if preference != "auto":
        pinned = by_provider.get(preference)
        if pinned is None:
            raise ValueError(f"unknown ASR provider {preference!r}")
        if not _supports(pinned, lang):
            raise ValueError(
                f"ASR provider {preference!r} does not support language {lang!r}"
            )
        return _decision(pinned)

    compatible = [option for option in options if _supports(option, lang)]
    installed = [option for option in compatible if option.installed]
    preferred_ids: tuple[str, ...]
    if lang in SENSEVOICE_LANGUAGES or lang is None:
        preferred_ids = (
            "sherpa-onnx-standard",
            "sherpa-onnx-qwen3",
            "sherpa-onnx-lite",
            "whisper-cpp-base",
        )
    else:
        preferred_ids = ("sherpa-onnx-qwen3", "whisper-cpp-base")

    for provider_id in preferred_ids:
        option = next((item for item in installed if item.provider_id == provider_id), None)
        if option is not None:
            return _decision(option)

    # Lite is installable and explicitly selectable but never the automatic
    # recommendation: the recorded ASR-4 release gate failed it (entity recall
    # below threshold; archive license unconfirmed) — see asr/approvals.py.
    recommended_ids: tuple[str, ...] = (
        ("sherpa-onnx-standard",) if lang in SENSEVOICE_LANGUAGES or lang is None
        else ("whisper-cpp-base",)
    )
    for provider_id in recommended_ids:
        option = by_provider.get(provider_id)
        if option is not None:
            return _decision(option)
    raise ValueError(f"no ASR provider supports language {lang!r}")


def _decision(option: ProviderOption) -> RoutingDecision:
    return RoutingDecision(
        provider_id=option.provider_id,
        model_id=option.model_id,
        model_version=option.model_version,
        tier=option.tier,
        installed=option.installed,
        recommendation=not option.installed,
        formal_default=option.formal_default,
    )
