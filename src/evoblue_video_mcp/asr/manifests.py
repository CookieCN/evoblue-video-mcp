"""Built-in production model manifests for all installable ASR choices.

These are the release-grade manifests the Model Manager and WebUI enumerate.
Archive models pin the measured upstream release and per-file digests. Qwen3-ASR
pins a reviewed ModelScope ONNX export commit plus every individual file digest,
which gives mainland users a resumable domestic path without adding PyTorch.
See ``docs/ASR_CHINA_SOURCE_QUALIFICATION.md``.
"""

from evoblue_video_mcp.asr.manifest import ModelManifest

_STANDARD_SENSEVOICE = {
    "model_id": "sensevoice-small-int8",
    "version": "2024-07-17",
    "provider": "sherpa-onnx",
    "languages": ["zh", "en", "ja", "ko", "yue"],
    "platforms": ["windows-x86_64"],
    "compressed_size_bytes": 163002883,
    "installed_size_bytes": 240506435,
    "license": "FunASR Model License 1.1",
    "attribution": "FunAudioLLM (Alibaba)",
    "upstream_url": "https://github.com/FunAudioLLM/SenseVoice",
    "redistribution": "upstream_only",
    "archive_format": "tar.bz2",
    "sources": [
        {
            "url": (
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
            ),
            "kind": "upstream",
            "sha256": "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
            "size_bytes": 163002883,
        }
    ],
    # The whitelist must cover EVERY file member of the pinned archive — the
    # extractor rejects undeclared members, and the first real install
    # (feedback #7, 2026-09-10) failed exactly because README.md/LICENSE/
    # export-onnx.py/test_wavs were missing here. Digests re-measured from the
    # verified archive (sha256 7d1e...347e) on 2026-09-10.
    "files": [
        {
            "name": "LICENSE",
            "size_bytes": 71,
            "sha256": "221c6df10b0931a5629adad671ea48fb7747e034c414b6d2bfa275bc3dd4ea17",
        },
        {
            "name": "README.md",
            "size_bytes": 104,
            "sha256": "763991a00edaea534ab36bf1b7cf89e61e911666dcfabbba71f91f9f7c593a63",
        },
        {
            "name": "export-onnx.py",
            "size_bytes": 5905,
            "sha256": "c97f6a33f9d7135efd4d55b3e24e288c47d925f3b4f04b8b3418c2821c0a89ce",
        },
        {
            "name": "model.int8.onnx",
            "size_bytes": 239233841,
            "sha256": "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        },
        {
            "name": "test_wavs/en.wav",
            "size_bytes": 228908,
            "sha256": "eb1eb008904465b74c304aad8342e8c7d3c6e61ffe9f66adcaca9cf0f76a93f4",
        },
        {
            "name": "test_wavs/ja.wav",
            "size_bytes": 230444,
            "sha256": "460bd8dccb0d2a5f4e29c628f837be4082d13defc64c3fc21dd1b6bb0e119095",
        },
        {
            "name": "test_wavs/ko.wav",
            "size_bytes": 147500,
            "sha256": "0dc797a5c81ed30fc339d91f3da718ab02854e17ffa37cb93c4c039ac5c6bb9c",
        },
        {
            "name": "test_wavs/yue.wav",
            "size_bytes": 164780,
            "sha256": "0960b2db54ae202071d250e6462fbf74a3c863f0e3e7f01273e4939c996875a0",
        },
        {
            "name": "test_wavs/zh.wav",
            "size_bytes": 178988,
            "sha256": "b77f1794fe374a0ba1ee1dc458bfaf9349496cbbfc32780c50ba3c5a7ad8e373",
        },
        {
            "name": "tokens.txt",
            "size_bytes": 315894,
            "sha256": "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
        },
    ],
}

_LITE_ZIPFORMER = {
    "model_id": "zipformer-ctc-small-zh-int8",
    "version": "2025-07-16",
    "provider": "sherpa-onnx",
    "languages": ["zh"],
    "platforms": ["windows-x86_64"],
    "compressed_size_bytes": 50536402,
    "installed_size_bytes": 63352462,
    "license": "Apache-2.0 (icefall/WenetSpeech upstream; exact archive carries no license file)",
    "attribution": "k2-fsa sherpa-onnx / icefall",
    "upstream_url": "https://github.com/k2-fsa/icefall",
    "redistribution": "upstream_only",
    "archive_format": "tar.bz2",
    "sources": [
        {
            "url": (
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
                "sherpa-onnx-zipformer-ctc-small-zh-int8-2025-07-16.tar.bz2"
            ),
            "kind": "upstream",
            "sha256": "6a71c0cc442ba85ac1455ca23e4561e2a1ef18e55269a184a649d54e9cd0524c",
            "size_bytes": 50536402,
        }
    ],
    # Full per-file whitelist of the pinned archive (re-measured 2026-09-10
    # from the verified archive, sha256 6a71...524c): the upstream ships
    # test_wavs alongside the model files and the extractor rejects members
    # that are not declared here.
    "files": [
        {
            "name": "bbpe.model",
            "size_bytes": 255180,
            "sha256": "503204e0690eff065e30d0e01898c9ab06d0e6dc376a741eb6846198f95b2f82",
        },
        {
            "name": "model.int8.onnx",
            "size_bytes": 62666860,
            "sha256": "32e5f17cc9a77d480f8d94bda97b7cc7a40965b6651b35385813f561a74129c8",
        },
        {
            "name": "test_wavs/0.wav",
            "size_bytes": 179646,
            "sha256": "668bf8df51a10027b84d5d8816a1ce11ae93545538dc05cfe2aa6811d399c250",
        },
        {
            "name": "test_wavs/1.wav",
            "size_bytes": 164976,
            "sha256": "30edbabea84ca4c076f5b43bb44495f436f6711319c32a200d3fc5c67c1fee1d",
        },
        {
            "name": "test_wavs/8k.wav",
            "size_bytes": 72434,
            "sha256": "0ed3bddacf0a23477d2b3c07ff9c24ad9c1fc3b31cdd47cb0773441e8e045cde",
        },
        {
            "name": "tokens.txt",
            "size_bytes": 13366,
            "sha256": "6fed8c6c248516f38e7faa19404b57413e8ce259f1cbc1fa4aebc86eac32fdfd",
        },
    ],
}

_QWEN3_ASR_06B = {
    "model_id": "qwen3-asr-0.6b-int8",
    "version": "2026-03-25",
    "provider": "sherpa-onnx",
    "languages": [
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
    ],
    "platforms": ["windows-x86_64"],
    "compressed_size_bytes": 987023031,
    "installed_size_bytes": 987023031,
    "license": "Apache-2.0",
    "attribution": "Qwen (Alibaba Cloud) / zengshuishui ONNX export",
    "upstream_url": "https://github.com/QwenLM/Qwen3-ASR",
    "redistribution": "mirror_approved",
    "archive_format": "file-set",
    "sources": [
        {
            "url": (
                "https://modelscope.cn/models/zengshuishui/"
                "Qwen3-ASR-onnx/resolve/"
                "9c182309f7bb075f241424441add9e16c5086dfb"
            ),
            "kind": "china-primary",
            "sha256": "ae2850217177b453be842cc12fd1bbde0e2fb93c369ff877e71e92e03aa6b8ac",
            "size_bytes": 987023031,
        }
    ],
    "files": [
        {
            "name": "conv_frontend.onnx",
            "source_path": "model_0.6B/conv_frontend.onnx",
            "size_bytes": 44148281,
            "sha256": "d22dc4423e0940e49884e903d2ea2f7e5567c14fc1aed97e4e26d6b8f208ef9e",
        },
        {
            "name": "decoder.int8.onnx",
            "source_path": "model_0.6B/decoder.int8.onnx",
            "size_bytes": 755914231,
            "sha256": "4f6885be5959ae26af3089d38ee7972c5fafbeeb1cf8d5e76eab6d8b61ca5771",
        },
        {
            "name": "encoder.int8.onnx",
            "source_path": "model_0.6B/encoder.int8.onnx",
            "size_bytes": 182491662,
            "sha256": "60748d3e6744a57c9c91e1b17424a6c2990567e8adceb0783940c03ed98fa9d9",
        },
        {
            "name": "tokenizer/chat_template.json",
            "source_path": "tokenizer/chat_template.json",
            "size_bytes": 1161,
            "sha256": "75a8cfca24f00de72d796fbfed6858fc9614ef3dabd8696684cc3bc03a9c58ff",
        },
        {
            "name": "tokenizer/config.json",
            "source_path": "tokenizer/config.json",
            "size_bytes": 6193,
            "sha256": "76d3ae4601ce939830b2517f4a6cadb86cc51316c3900af6b020b051c21a478c",
        },
        {
            "name": "tokenizer/merges.txt",
            "source_path": "tokenizer/merges.txt",
            "size_bytes": 1671853,
            "sha256": "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
        },
        {
            "name": "tokenizer/preprocessor_config.json",
            "source_path": "tokenizer/preprocessor_config.json",
            "size_bytes": 330,
            "sha256": "45e120a4eda2c20c5d7f2ea9354e63536bf35e27aa573fb7cdf78017b378770d",
        },
        {
            "name": "tokenizer/tokenizer_config.json",
            "source_path": "tokenizer/tokenizer_config.json",
            "size_bytes": 12487,
            "sha256": "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c",
        },
        {
            "name": "tokenizer/vocab.json",
            "source_path": "tokenizer/vocab.json",
            "size_bytes": 2776833,
            "sha256": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
        },
    ],
}

_WHISPER_CPP_BASE = {
    "model_id": "whisper-cpp-base",
    "version": "80da2d8",
    "provider": "whisper.cpp",
    "languages": ["*"],
    "platforms": ["windows-x86_64", "macos-x86_64", "macos-arm64", "linux-x86_64"],
    "compressed_size_bytes": 147951465,
    "installed_size_bytes": 147951465,
    "license": "MIT",
    "attribution": "OpenAI Whisper / ggml-org whisper.cpp conversion",
    "upstream_url": "https://github.com/ggml-org/whisper.cpp",
    "redistribution": "upstream_only",
    "archive_format": "raw",
    "sources": [
        {
            "url": (
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
                "80da2d8bfee42b0e836fc3a9890373e5defc00a6/ggml-base.bin"
            ),
            "kind": "upstream",
            "sha256": "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
            "size_bytes": 147951465,
        }
    ],
    "files": [
        {
            "name": "ggml-base.bin",
            "size_bytes": 147951465,
            "sha256": "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
        }
    ],
}

BUILTIN_MANIFESTS: tuple[ModelManifest, ...] = tuple(
    ModelManifest.model_validate(data)
    for data in (_LITE_ZIPFORMER, _STANDARD_SENSEVOICE, _QWEN3_ASR_06B, _WHISPER_CPP_BASE)
)

_MANIFEST_BY_ID: dict[str, ModelManifest] = {
    manifest.model_id: manifest for manifest in BUILTIN_MANIFESTS
}


def get_builtin_manifest(model_id: str) -> ModelManifest | None:
    """Return a validated built-in manifest by model id, or ``None``."""
    return _MANIFEST_BY_ID.get(model_id)
