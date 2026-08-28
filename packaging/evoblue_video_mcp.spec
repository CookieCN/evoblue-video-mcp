# PyInstaller onedir spec for the EvoBlue Video MCP Local Engine.
#
# Two variants are produced from this one spec via EVOBLUE_PKG_VARIANT:
#   base — core engine only: no sherpa-onnx, no onnxruntime, no numpy. The ASR
#          tiers simply stay unregistered (graceful degradation), so subtitle
#          workflows keep working and the ASR-installer delta stays measurable.
#   full — base + the sherpa-onnx ASR runtime (sherpa_onnx, onnxruntime with
#          its capi DLLs, numpy) and the bundled Silero VAD asset, enabling the
#          Lite/Standard tiers after a Model Manager download.
#
# Model weights are NEVER bundled in either variant (ASR_PLAN section 3).

import os

variant = os.environ.get("EVOBLUE_PKG_VARIANT", "full")
if variant not in ("base", "full"):
    raise SystemExit(f"unknown EVOBLUE_PKG_VARIANT: {variant!r}")

is_full = variant == "full"

datas = []
hiddenimports = [
    # uvicorn's dynamically-selected pieces (uvicorn[standard])
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # sqlalchemy resolves the sqlite dialect and its async driver at runtime
    "sqlalchemy.dialects.sqlite",
    "aiosqlite",
    # keyring's platform backend is imported lazily
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.chainer",
]
if is_full:
    hiddenimports += ["sherpa_onnx", "onnxruntime", "numpy"]
    datas += [
        (
            "../src/evoblue_video_mcp/asr/assets/silero_vad.onnx",
            "evoblue_video_mcp/asr/assets",
        ),
        (
            "../src/evoblue_video_mcp/asr/assets/silero_vad.LICENSE",
            "evoblue_video_mcp/asr/assets",
        ),
        (
            "../src/evoblue_video_mcp/asr/assets/silero_vad.README.md",
            "evoblue_video_mcp/asr/assets",
        ),
    ]
else:
    # Fail closed at analysis time: if base accidentally pulls numpy in via
    # some future import chain, this exclusion makes the build break loudly
    # instead of silently shipping an ASR runtime in the base installer.
    excludes = ["sherpa_onnx", "onnxruntime", "numpy"]

a = Analysis(
    ["entrypoint.py"],
    pathex=["../src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes if not is_full else [],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=f"evoblue-engine-{variant}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=f"evoblue-video-mcp-{variant}",
)
