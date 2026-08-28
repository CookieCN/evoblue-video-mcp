"""Generate the frozen EvoBlue ASR benchmark corpus v1 on this machine.

The corpus must be permissioned (ASR_PLAN section 6), so it is synthesized
locally instead of recorded from copyrighted video: Windows SAPI voices speak
the reference transcripts, and numpy mixes deterministic noise/music variants.
The transcript doubles as ground truth by construction, and named entities are
declared per sample for recall scoring. Generated audio is written to
``benchmarks/corpus/`` (gitignored); the manifest pins per-file SHA-256 so a
gate report can be tied to the exact audio it measured.

Coverage v1 (automatable subset): Mandarin studio / noise / music-bed / entity
dense / long multi-sentence, English studio, mixed Chinese-English, silence,
music-only. Real accents, overlapping speakers and livestream conditions are
NOT covered and are recorded as corpus limitations in docs/ASR_RELEASE_GATE.md.

Usage:
    uv run python scripts/build_benchmark_corpus.py --out benchmarks/corpus
"""

# Sample texts are Chinese/English data; fullwidth punctuation is intentional.
# ruff: noqa: RUF001

import argparse
import hashlib
import json
import sys
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000

_ZH_VOICE = "Microsoft Huihui Desktop - Chinese (Simplified)"
_EN_VOICE = "Microsoft Zira Desktop - English (United States)"

# (id, language, voice, mixing, text, entities)
# ``mixing`` selects the post-processing variant applied to the synthesized
# speech; entity samples deliberately stress brand/person/number reading.
_SAMPLES: list[tuple[str, str, str, str, str, tuple[str, ...]]] = [
    (
        "zh-studio-001",
        "zh",
        _ZH_VOICE,
        "clean",
        "大家好，欢迎收看本期节目。今天我们聊一聊跨境电商的趋势。",
        ("跨境电商",),
    ),
    (
        "zh-studio-002",
        "zh",
        _ZH_VOICE,
        "clean",
        "上一期视频发布之后，很多朋友在评论区问我工具的使用方法。",
        (),
    ),
    (
        "zh-noise-001",
        "zh",
        _ZH_VOICE,
        "noise",
        "在选品的时候，一定要先看市场需求，再看竞争强度。",
        ("选品",),
    ),
    (
        "zh-music-001",
        "zh",
        _ZH_VOICE,
        "music",
        "欢迎回到频道，别忘了订阅和点赞。我们马上进入今天的正题。",
        (),
    ),
    (
        "zh-entity-001",
        "zh",
        _ZH_VOICE,
        "clean",
        "亚马逊在二零二四年推出了新的广告工具，TikTok Shop 也在快速扩张。",
        ("亚马逊", "TikTok Shop"),
    ),
    (
        "zh-entity-002",
        "zh",
        _ZH_VOICE,
        "noise",
        "Wilson 的团队用三个月时间把独立站的复购率提升到了百分之三十二。",
        ("Wilson", "独立站"),
    ),
    (
        "zh-long-001",
        "zh",
        _ZH_VOICE,
        "clean",
        "第一，做好内容定位。第二，建立稳定的更新节奏。"
        "第三，把每一条视频都当成一个产品来打磨。"
        "第四，用数据复盘每一次发布。第五，把有效的方法沉淀成流程。",
        (),
    ),
    (
        "en-studio-001",
        "en",
        _EN_VOICE,
        "clean",
        "Welcome back to the channel. Today we will talk about cross border marketing.",
        ("cross border marketing",),
    ),
    (
        "zh-en-001",
        "zh",
        _ZH_VOICE,
        "clean",
        "很多品牌都在用 ChatGPT 写文案，但是真正做好本地化的还是少数。",
        ("ChatGPT",),
    ),
    (
        "zh-silence-001",
        "zh",
        "",
        "silence",
        "",
        (),
    ),
    (
        "zh-musiconly-001",
        "zh",
        "",
        "music_only",
        "",
        (),
    ),
]


def _sapi_synthesize(text: str, voice: str, out_path: Path) -> None:
    """Speak ``text`` to a WAV via SAPI in the voice's default format.

    Requesting a non-native rate from the desktop voices produced a saturated,
    unusable waveform, so we synthesize at the default rate (22050 Hz) and
    resample ourselves below.
    """
    import win32com.client

    sapi = win32com.client.Dispatch("SAPI.SpVoice")
    target = None
    for v in sapi.GetVoices():
        if v.GetDescription() == voice:
            target = v
            break
    if target is None:
        raise SystemExit(f"SAPI voice not found: {voice!r}")

    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = 0  # SAFTDefault: native voice rate, 16-bit mono
    stream.Open(str(out_path), 3)  # SSFMCreateForWrite
    sapi.Voice = target
    sapi.AudioOutputStream = stream  # without this, Speak goes to the speakers
    sapi.Speak(text)  # synchronous: returns only after the stream is fully written
    stream.Close()
    sapi.AudioOutputStream = None


def _load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, rate


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resample; deterministic and artifact-free enough
    for clear TTS speech."""
    if src_rate == dst_rate:
        return samples
    duration = len(samples) / src_rate
    target_len = round(duration * dst_rate)
    if target_len == 0:
        return np.zeros(0, dtype=np.float32)
    src_idx = np.arange(target_len, dtype=np.float32) * (src_rate / dst_rate)
    left = np.clip(src_idx.astype(np.int64), 0, len(samples) - 1)
    right = np.clip(left + 1, 0, len(samples) - 1)
    frac = (src_idx - left).astype(np.float32)
    return samples[left] * (1.0 - frac) + samples[right] * frac


def _write_wav(path: Path, samples: np.ndarray) -> None:
    data = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((data * 32767.0).astype(np.int16).tobytes())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mixing(mix: str, speech: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Deterministic post-processing variants (seeded, same output every run)."""
    if mix == "clean":
        return speech
    if mix == "noise":
        noise = rng.normal(0.0, 0.02, size=len(speech)).astype(np.float32)
        return speech * 0.9 + noise
    if mix == "music":
        t = np.arange(len(speech), dtype=np.float32) / SAMPLE_RATE
        chord = (
            np.sin(2 * np.pi * 220.0 * t)
            + np.sin(2 * np.pi * 277.18 * t)
            + np.sin(2 * np.pi * 329.63 * t)
        ).astype(np.float32)
        envelope = 0.06 * (1.0 + 0.2 * np.sin(2 * np.pi * 0.5 * t))
        return speech * 0.92 + chord * envelope
    if mix == "music_only":
        t = np.arange(len(speech) or SAMPLE_RATE * 3, dtype=np.float32) / SAMPLE_RATE
        chord = (
            np.sin(2 * np.pi * 196.0 * t)
            + np.sin(2 * np.pi * 246.94 * t)
        ).astype(np.float32)
        envelope = 0.12 * (1.0 + 0.3 * np.sin(2 * np.pi * 0.4 * t))
        return chord * envelope
    if mix == "silence":
        return np.zeros(SAMPLE_RATE * 3, dtype=np.float32)
    raise SystemExit(f"unknown mixing variant: {mix!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="benchmarks/corpus", help="output directory")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        raise SystemExit("corpus generation requires Windows SAPI voices")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "_tts"
    tmp_dir.mkdir(exist_ok=True)

    entries = []
    for sample_id, language, voice, mix, text, entities in _SAMPLES:
        if voice:
            tts_path = tmp_dir / f"{sample_id}-tts.wav"
            _sapi_synthesize(text, voice, tts_path)
            speech, tts_rate = _load_wav(tts_path)
            speech = _resample(speech, tts_rate, SAMPLE_RATE)
        else:
            speech = np.zeros(0, dtype=np.float32)
        rng = np.random.default_rng(20260828)
        audio = _mixing(mix, speech, rng)
        wav_path = out_dir / f"{sample_id}.wav"
        _write_wav(wav_path, audio)
        entries.append(
            {
                "id": sample_id,
                "description": f"synthesized {mix} sample",
                "language": language,
                "reference_text": text,
                "entities": list(entities),
                "audio_path": str(wav_path.resolve()),
                "audio_sha256": _sha256(wav_path),
            }
        )
        print(f"{sample_id}: {len(audio) / SAMPLE_RATE:.2f}s")

    manifest = {
        "name": "evoblue-asr-gate-v1",
        "version": "1",
        "samples": entries,
    }
    manifest_path = out_dir / "corpus.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
