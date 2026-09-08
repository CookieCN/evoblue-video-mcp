# EvoBlue ASR Development Plan

Status: delivered; amended 2026-09-08 for optional Qwen3-ASR
Decision date: 2026-08-26  
Owner intent: reduce the default package size and make first-time model installation reliable for users in China without weakening local-first privacy.

## 1. Decision

EvoBlue will not ship `faster-whisper` as a mandatory dependency or use it as the only ASR implementation.

The planned ASR stack is:

1. Platform subtitles remain the first choice. ASR is entered only when usable subtitles are unavailable.
2. Chinese-first onboarding uses two independently installable sherpa-onnx model tiers: a small Zipformer CTC INT8 model is the Lite candidate, and SenseVoiceSmall INT8 is the Standard candidate.
3. `whisper.cpp` is the optional multilingual compatibility provider for languages outside the selected sherpa-onnx model's coverage.
4. Qwen3-ASR 0.6B INT8 is an optional sherpa-onnx multilingual/dialect enhancement. It is installed from a pinned ModelScope file set for mainland users, but remains non-default until its independent release gate passes.
5. `faster-whisper` may remain an optional advanced provider, but it must not increase the base installer size.
6. Cloud ASR is an optional BYOK provider and must never be silently selected or required for the local workflow.

This is a product architecture decision, not final recognition-quality approval. The default provider can change only after the benchmark gate in section 7 is completed.

## 2. Why

The primary conflict is not model accuracy alone. EvoBlue must balance local privacy, a small installer, reliable downloads in mainland China, strong Chinese recognition, and broad language coverage for YouTube content. No single model wins all five dimensions, so the solution is a provider contract plus language-aware routing and independently downloadable model packs.

Planning figures to recheck and pin during implementation:

| Candidate | Verified installed model size | Coverage | Planned role |
|---|---:|---|---|
| sherpa-onnx Zipformer CTC small INT8 (offline, 2025-07-16) | 63.4 MB repository total; 62.7 MB ONNX | Chinese only | Lite first-experience candidate |
| sherpa-onnx Zipformer CTC small INT8 (streaming, 2025-04-01) | approximately 25 MB ONNX | Chinese only | benchmark challenger, not a second automatic download |
| SenseVoiceSmall INT8 for sherpa-onnx | approximately 228 MB ONNX | Chinese, English, Japanese, Korean, Cantonese | Standard Chinese-first/mixed-language candidate |
| Qwen3-ASR 0.6B INT8 for sherpa-onnx | 987,023,031 bytes file set | 30 languages + 22 Chinese dialects | optional multilingual/dialect enhancement; non-default |
| whisper.cpp base | 142 MiB model | multilingual | lightweight compatibility pack |
| whisper.cpp small | 466 MiB model | multilingual | optional quality pack |
| faster-whisper | model plus CTranslate2/native dependencies | multilingual | optional advanced provider only |

The previous 1.05 GB observation came from the full SenseVoice archive, which contains both the approximately 894 MB FP32 model and the approximately 228 MB INT8 model. EvoBlue must use the upstream INT8-only artifact; downloading or retaining the FP32 file is a release-blocking manifest error. The figures above are decision inputs, not network-size guarantees. Release manifests must contain measured archive download size, selected installed-file size and SHA-256.

## 3. User-visible behavior

- The base installer contains no ASR model weights.
- Subtitle-only workflows never trigger an ASR download.
- When ASR is required but no model is installed, the persisted job enters `waiting_for_model` instead of failing or downloading invisibly.
- WebUI shows model purpose, languages, download size, installed size and disk location before consent.
- Download progress, pause, resume, retry, cancellation, checksum verification and uninstall are visible.
- After atomic installation, waiting jobs may resume through normal Worker recovery.
- Chinese and SenseVoice-covered users are first offered the formally approved Standard model; Lite remains explicitly installable but is not recommended after failing its gate.
- Mixed Chinese-English, Cantonese, Japanese or Korean inputs are offered Standard directly; languages outside its coverage are offered an optional whisper.cpp pack.
- Qwen3-ASR appears as an explicit approximately 941 MiB option. Installing it never changes old jobs; when present, automatic routing may reuse it for declared supported languages, but it is not a first-download recommendation.
- Unknown language does not trigger multiple large downloads automatically.
- A model upgrade is explicit and never downloads Lite and Standard together unless the user chooses to keep both.

## 4. Architecture contract

### 4.1 ASR provider boundary

Local Engine owns an ASR provider interface. Pipeline and Worker must not import provider-specific libraries directly.

Minimum logical contract:

```python
class ASRProvider(Protocol):
    provider_id: str

    async def inspect(self) -> ASRCapabilities: ...
    async def transcribe(self, request: ASRRequest) -> ASRResult: ...
```

The concrete type names may change, but the contract must support provider/model/version identity, supported languages, absolute segment timestamps, cancellation, progress, structured redacted errors, deterministic missing-model discovery and normalized output.

`ASRResult` must include detected language when available, ordered transcript segments, segment start/end timestamps, provider/model/version and warnings. Provider-specific raw output stays outside the stable report contract.

### 4.2 Long-video segmentation

SenseVoice long audio is segmented with VAD before recognition. Subtitle segment timing uses VAD boundaries; token timestamps may enrich results but are not the sole source of end times.

Required properties:

- deterministic segmentation for the same configuration;
- absolute video-time reconstruction;
- bounded maximum segment duration;
- safe merging without losing source timing;
- checkpointed completed segments so a crash does not restart a long video from zero.

### 4.3 Model Manager

Model acquisition is a Local Engine subsystem, not provider-side ad hoc download logic.

Each model manifest contains stable model/version IDs, provider compatibility, languages, compressed and installed sizes, ordered sources, SHA-256, license/attribution, upstream URL, platform requirements and explicit artifact-license/distribution statuses. Distribution mode is one of `upstream_only`, `mirror_approved` or `blocked`; a code-repository license is not sufficient evidence for model weights or converted artifacts.

Download requirements:

- China-optimized primary source and upstream fallback;
- resumable partial downloads with bounded retries;
- checksum verification before extraction;
- staging-directory extraction and atomic promotion;
- path-traversal and archive-bomb protection;
- preservation of the last working version during upgrade;
- explicit uninstall showing reclaimable space.

Do not use Hugging Face or GitHub Releases as the only mainland-China source. Before hosting weights on an EvoBlue CDN, verify redistribution terms and preserve attribution. Prefer reproducible conversion from official weights over unverified third-party conversion.

### 4.4 Persisted state

The Job model must distinguish missing model, download failure, unsupported provider/language and ASR failure. Exact database changes require a versioned migration. Worker lease recovery tests must cover `waiting_for_model`, model installation and segment checkpoints.

## 5. Delivery phases

### ASR-0 — Contract and benchmark fixture

Prerequisite: current Worker and pipeline contracts are stable enough to add a provider boundary.

Deliverables:

- ASR provider protocol and normalized result schema;
- deterministic fake provider for tests;
- benchmark corpus manifest and scoring command;
- model manifest schema;
- redistribution/download-source architecture decision record.

Acceptance:

- pipeline tests contain no SenseVoice or Whisper imports;
- manifest validation rejects unknown hashes, unsafe paths and incompatible platforms;
- benchmark results are reproducible by a documented command.

### ASR-1 — sherpa-onnx Lite/Standard proof of capability

Prerequisite: ASR-0.

Deliverables:

- one sherpa-onnx provider boundary capable of loading the Lite Zipformer CTC and Standard SenseVoice model families without leaking model-specific imports into the pipeline;
- Lite offline Zipformer CTC small INT8 and Standard SenseVoiceSmall INT8 manifests;
- VAD segmentation and absolute timestamps;
- Windows x86-64 CPU support first;
- deterministic Windows ONNX Runtime DLL selection before importing sherpa-onnx;
- checkpointed long-video transcription;
- license/attribution inventory.

Acceptance:

- offline transcription works after installing either model tier;
- no manifest downloads the full SenseVoice FP32+INT8 archive, and installed files contain no unused FP32 weights;
- Windows real-engine tests prove that sherpa does not bind to an obsolete System32 `onnxruntime.dll`;
- 60-minute input produces monotonic bounded segments;
- cancellation leaves a recoverable checkpoint;
- Torch, torchaudio, CTranslate2 and faster-whisper stay out of the base runtime;
- errors and logs pass redaction tests.

### ASR-2 — Model Manager and China download path

Prerequisites: ASR-0 and the ASR-1 Lite/Standard artifact inventory. ASR-1 proof of capability is complete; ASR-2 must now freeze the release-grade manifest before downloader implementation.

Deliverables:

- model list/install/update/uninstall APIs;
- persisted resumable download state;
- WebUI model-management flow;
- China primary source and upstream fallback;
- checksum, safe extraction and atomic promotion;
- artifact-level license evidence and a release gate that rejects unapproved redistribution sources.

Implementation order:

1. freeze the manifest and persisted download-state contracts, including redistribution status;
2. implement the safe resumable downloader and atomic installer;
3. expose consent, progress, retry, cancel and uninstall in the WebUI;
4. qualify mainland-China sources and upstream fallback with measured results and archived license evidence.

Acceptance:

- interrupted downloads resume when range requests are supported;
- checksum mismatch never activates a model;
- failed upgrade preserves the last working model;
- users see network/storage cost before consent;
- subtitle-only workflows download nothing;
- an artifact without sufficient use terms cannot enter a production manifest; an unapproved mirror cannot be listed, while `upstream_only` permits only the pinned original publisher URL;
- archive traversal, undeclared files and decompression bombs are rejected before activation.

### ASR-3 — Tier routing and multilingual fallback

Prerequisites: ASR-1 and ASR-2.

Deliverables:

- configurable provider and Lite/Standard tier routing;
- optional whisper.cpp provider with one pinned multilingual manifest;
- explicit unsupported-language handling;
- provider/model metadata in generated Markdown.

Acceptance:

- Chinese-only onboarding can complete with a single Lite download;
- upgrading from Lite to Standard is explicit, resumable and does not invalidate existing jobs or Markdown;
- SenseVoice languages do not require Whisper;
- unsupported languages receive an install recommendation, not a generic failure;
- a user-pinned provider overrides routing;
- no provider starts a hidden download.

### ASR-4 — Release hardening

Prerequisites: ASR-3 and the benchmark gate.

Deliverables:

- Windows/macOS/Linux packaging matrix;
- CPU compatibility tests;
- CDN monitoring and fallback drill;
- third-party notices in installer/WebUI;
- migration and rollback documentation.

Acceptance:

- base installer delta is measured for every optional provider;
- clean-machine installation passes a mainland-China network profile;
- artifacts are reproducible, pinned and hash-verified;
- provider failure cannot corrupt Job state or completed Markdown.

## 6. EvoBlue benchmark corpus

Vendor benchmarks are insufficient. Build a permissioned EvoBlue test set containing Mandarin studio speech, livestreams, interviews, background music, regional accents, mixed Chinese-English technical/business terms, accented English, Cantonese, a 60-minute video, silence/music-only clips, overlapping speakers, low volume and corrupt input.

Track:

- Chinese CER and English WER;
- named-entity accuracy;
- segment boundary error and invalid/overlapping timestamps;
- real-time factor on defined CPU tiers;
- peak memory;
- download success/duration by source;
- installed size and base-installer delta;
- crash recovery and resumed-work ratio.

## 7. Default-provider release gate

Lite becomes the Chinese first-experience default only if it passes the onboarding gate below. SenseVoice becomes the Standard recommendation only if it passes the quality gate below.

Lite onboarding gate:

1. The selected offline/streaming Zipformer candidate wins on the frozen EvoBlue Chinese-video corpus; only one ships in the production catalog.
2. Chinese CER, named-entity accuracy and segment readability meet a separately frozen Lite threshold; UI labels it as fast trial quality rather than best quality.
3. Its release manifest uses measured archive/download and installed sizes, an approved license, pinned hashes and a verified China source.
4. Mixed-language or non-Chinese inputs are not silently routed to Lite.

Standard quality gate:

1. Chinese and mixed Chinese-English quality is not materially worse than the current faster-whisper baseline; freeze thresholds before the final comparison.
2. Timestamps produce readable Markdown/SRT segmentation.
3. CPU speed and UI expectations are acceptable on the minimum Windows machine.
4. Peak memory fits that machine with Engine and WebUI running.
5. Installation succeeds through the China source and checksum/fallback path.
6. Redistribution and attribution have a recorded review.
7. Base packaging contains neither model weights nor unused heavyweight ASR frameworks.

Failure policy:

- quality failure: SenseVoice remains a fast option; whisper.cpp/faster-whisper supplies quality mode;
- Lite quality failure: do not expose it as the recommended first experience; offer the INT8-only SenseVoice Standard pack instead;
- timestamp failure: fix VAD/segmentation before changing the stable report contract;
- distribution failure: leave the Job waiting and expose manual recovery; never hide a fallback download;
- license failure: distribute only an approved-source manifest or select another model.

## 8. First-implementation non-goals

- bundled GPU/CUDA runtimes;
- speaker diarization;
- multi-provider voting;
- downloading all language packs during setup;
- downloading the full SenseVoice archive containing unused FP32 weights;
- model fine-tuning;
- silent cloud audio upload;
- removing the provider abstraction after one adapter works.

## 9. Sources to revalidate

- https://github.com/FunAudioLLM/SenseVoice
- https://github.com/k2-fsa/sherpa-onnx/blob/master/docs/source/onnx/sense-voice/pretrained.rst
- https://github.com/k2-fsa/sherpa/blob/master/docs/source/onnx/sense-voice/python-api.rst
- https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md
- https://github.com/QwenLM/Qwen3-ASR
- https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html
- https://modelscope.cn/models/zengshuishui/Qwen3-ASR-onnx
- https://github.com/FunAudioLLM/SenseVoice/issues/286

Versions, URLs and licenses can change. The implementation agent must revalidate them before pinning a release manifest.
