# silero_vad.onnx

Bundled runtime dependency of the sherpa-onnx ASR tiers (`asr` extra). The
recognition archives (Lite Zipformer / Standard SenseVoice) ship no VAD model,
yet long-audio segmentation requires one, so it is delivered with the package
instead of as a user-installed model.

- Source: https://github.com/snakers4/silero-vad (upstream copy fetched via the
  k2-fsa sherpa-onnx model release)
- Size: 643,854 bytes
- SHA-256: `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`
- License: MIT (c) 2020-present Silero Team — full text in
  `silero_vad.LICENSE` (this directory), THIRD_PARTY_NOTICES.md, and
  `docs/ASR_MODEL_LICENSES.md`

The hash is pinned in `evoblue_video_mcp.asr.vad` and verified (fail-closed)
before a sherpa provider may register. Do not replace this file without
updating the pinned constant, this README, and the license records.
