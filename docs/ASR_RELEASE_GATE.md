# ASR Release Gate — Evidence & Verdict (ASR-4)

Status: executed
Date: 2026-08-28
Machine: Windows 11 Pro 10.0.22631, x86-64, 12 logical CPUs, Python 3.12
Contract: `docs/ASR_PLAN.md` section 7
Corpus: TTS-synthesized EvoBlue corpus v1 (frozen; regenerated locally, never committed)
Thresholds: frozen v1 in `src/evoblue_video_mcp/asr/release_gate.py`

## Verdict

| Tier | Model / Version | Gate result | Formal default? |
|---|---|---|---|
| Standard | `sensevoice-small-int8` @ `2024-07-17` | **PASS** (all 5 items) | **Yes** |
| Lite | `zipformer-ctc-small-zh-int8` @ `2025-07-16` | **FAIL** (entity recall 0.50 < 0.60) | No |
| Multilingual fallback | `whisper-cpp-base` @ `80da2d8` | Not measured (no pinned CLI runtime) | No |

The machine-readable approval record lives in `src/evoblue_video_mcp/asr/approvals.py`, keyed
by exact `(model_id, version)`. A republished or bumped model version starts unapproved and
must re-run this gate.

## Method

1. `uv run python scripts/build_benchmark_corpus.py --out benchmarks/corpus` — synthesizes the
   permissioned corpus with local Windows SAPI voices (Huihui zh-CN, Zira en-US): the reference
   transcript is ground truth by construction. Variants: clean Mandarin, plus white-noise mix,
   plus music bed, entity-dense brand/number text, long multi-sentence, clean English,
   mixed Chinese-English, pure silence, music-only. Audio is resampled to 16 kHz mono PCM16
   and pinned per-file SHA-256 inside the generated `corpus.json`.
2. `uv run python -m evoblue_video_mcp.asr.benchmark benchmarks/corpus/corpus.json
   --provider standard --gate --out benchmarks/results/standard-v1.json` (and `--provider lite`).
   Real tiers load through the production registration chain (model files + bundled VAD
   SHA verification), not a benchmark shortcut.
3. Scoring: CER/WER after NFKC + case + punctuation normalization (`metrics.normalize_for_scoring`),
   entity recall over declared entities, real-time factor (transcribe seconds / audio seconds),
   hallucination rate on empty-reference samples, peak process RSS.

## Corpus v1 limitations (recorded, deliberate)

The corpus is fully permissioned and reproducible, which trades away some real-world coverage.
**Not** covered by v1: regional accents, overlapping speakers, livestream noise, low-volume
recordings, corrupt inputs, and a 60-minute endurance sample. TTS conditions are a quality
*lower bound*; these results must not be marketed as best-quality proof on real user audio.
Extending the corpus with consented real recordings re-opens the gate; the thresholds stay
frozen per version and a corpus change bumps the corpus version.

## Measured results (2026-08-28)

### Standard — SenseVoiceSmall INT8 (`benchmarks/results/standard-v1.json`)

| Gate item | Measured | Threshold | Result |
|---|---:|---:|---|
| Chinese CER (8 zh samples) | 0.0000 | ≤ 0.10 | PASS |
| English WER (1 en sample) | 0.0769 | ≤ 0.15 | PASS |
| Entity recall (6 entity-bearing samples) | 1.0000 | ≥ 0.80 | PASS |
| Real-time factor (mean) | 0.0598 | ≤ 0.30 | PASS |
| Hallucination on silence/music-only | 0.0000 | = 0 | PASS |
| Peak process RSS | 537 MB | — | recorded |

### Lite — Zipformer CTC small INT8 (`benchmarks/results/lite-v1.json`)

| Gate item | Measured | Threshold | Result |
|---|---:|---:|---|
| Chinese CER (8 zh samples) | 0.0824 | ≤ 0.25 | PASS |
| English WER | out of scope (zh-only tier) | — | n/a |
| Entity recall (6 entity-bearing samples) | **0.5000** | ≥ 0.60 | **FAIL** |
| Real-time factor (mean) | 0.0273 | ≤ 0.15 | PASS |
| Hallucination on silence/music-only | 0.0000 | = 0 | PASS |
| Peak process RSS | 636 MB | — | recorded |

Lite's failures concentrate exactly where a small CTC model is expected to struggle: English
brand names embedded in Chinese/mixed text ("TikTok Shop", "ChatGPT") and entity-heavy sentences
(CER 0.18–0.24 on those samples). General clean-Mandarin dictation is fine (CER 0.00–0.02).

## Consequences (per `docs/ASR_PLAN.md` section 7 failure policy)

- **Standard is the formal default** for Chinese and all SenseVoice-covered languages. The WebUI
  shows it with the formal-default label; automatic routing already recommended it.
- **Lite fails its onboarding gate** (quality item) — it is *not* exposed as a recommended first
  experience. It remains installable and explicitly selectable; the WebUI label states this
  plainly. Independent second reason, unchanged: the exact archive carries no license file
  (`docs/ASR_MODEL_LICENSES.md`), so even a quality pass would not have approved it.
- **whisper.cpp Base** remains the documented out-of-coverage fallback recommendation with a
  clean MIT license; no formal default claim is made because no benchmark has been measured.
- Routing recommendations are unchanged; the *rationale* moved from "license unconfirmed" to the
  recorded gate verdict (`asr/routing.py`, `asr/approvals.py`).

## Zipformer streaming challenger (gate item: only one Lite candidate ships)

The offline Zipformer CTC small (2025-07-16) is the only Zipformer in the production catalog.
The streaming Zipformer candidate (~25 MB) was **not** benchmarked: it requires a separate
sherpa-onnx online-recognizer provider integration, is not in the catalog, and would not ship.
Comparing candidates is only meaningful when both could actually ship; if the streaming model
ever becomes a catalog candidate, this gate must run for both before either is approved.

## Harness-validation trail (why the first gate runs were discarded)

Two gate runs were discarded before the recorded ones, both caused by the benchmark harness,
not the models — documented so a future reader trusts but also audits the pipeline:

1. SAPI synthesis with a forced 16 kHz output format produced a saturated, unusable waveform
   (constant RMS ≈ 0.996); models scored CER 0.5–0.9. Fixed by synthesizing at the voice's
   native 22.05 kHz and resampling with numpy.
2. WER scoring consumed the CER normalization that strips all whitespace, collapsing every
   transcript into one token (WER ≡ 1.0). Fixed with a token-boundary-preserving
   `normalize_words`; CER keeps the whitespace-free normalization.

Reproducibility: rerunning steps 1–2 above on the same machine/voice versions regenerates
equivalent audio (same recipe, pinned SHAs recorded per run in the generated manifest) and
byte-identical scoring logic; reports embed the platform, CPU count, thresholds version and
peak RSS for audit.
