"""Trusted artifact catalog: model/version -> approved download sources.

Each entry pins the exact URL, source kind, SHA-256 and compressed size for a
reviewed artifact. A manifest is releasable only when its ``(model_id, version)``
and every source ``(url, kind, sha256, size_bytes)`` match an entry exactly, so
an arbitrary URL or a tampered digest cannot pass.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ApprovedSource:
    url: str
    kind: str
    sha256: str
    size_bytes: int


APPROVED_CATALOG: dict[tuple[str, str], frozenset[ApprovedSource]] = {
    ("qwen3-asr-0.6b-int8", "2026-03-25"): frozenset(
        {
            ApprovedSource(
                url=(
                    "https://modelscope.cn/models/zengshuishui/"
                    "Qwen3-ASR-onnx/resolve/"
                    "9c182309f7bb075f241424441add9e16c5086dfb"
                ),
                kind="china-primary",
                sha256="ae2850217177b453be842cc12fd1bbde0e2fb93c369ff877e71e92e03aa6b8ac",
                size_bytes=987023031,
            )
        }
    ),
    ("sensevoice-small-int8", "2024-07-17"): frozenset(
        {
            ApprovedSource(
                url=(
                    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
                ),
                kind="upstream",
                sha256="7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
                size_bytes=163002883,
            ),
        }
    ),
    ("zipformer-ctc-small-zh-int8", "2025-07-16"): frozenset(
        {
            ApprovedSource(
                url=(
                    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                    "sherpa-onnx-zipformer-ctc-small-zh-int8-2025-07-16.tar.bz2"
                ),
                kind="upstream",
                sha256="6a71c0cc442ba85ac1455ca23e4561e2a1ef18e55269a184a649d54e9cd0524c",
                size_bytes=50536402,
            ),
        }
    ),
    ("whisper-cpp-base", "80da2d8"): frozenset(
        {
            ApprovedSource(
                url=(
                    "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
                    "80da2d8bfee42b0e836fc3a9890373e5defc00a6/ggml-base.bin"
                ),
                kind="upstream",
                sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
                size_bytes=147951465,
            )
        }
    ),
}
