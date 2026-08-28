"""Guards: the core package must not depend on concrete ASR engines.

Two complementary guards enforce the provider boundary:

1. Static import scan — catches the most common mistake, a stray
   ``import sherpa_onnx`` (or a literal ``importlib.import_module("sherpa_onnx")``)
   in Pipeline/Worker. Engine imports are allowed only under ``asr/providers/``,
   where ASR-1 adapters will live.

2. Base-dependency scan — keeps heavy ASR libraries out of the base install.

Source scanning cannot catch a *variable*-name dynamic import (e.g.
``importlib.import_module(provider_module)``), so strict isolation additionally
relies on a provider registry (ASR-1): the core only ever obtains providers
through the registry, never by importing engine libraries itself.
"""

import re
import tomllib
from pathlib import Path

ENGINE_TOKENS = re.compile(
    r"\b(sherpa_onnx|sherpa|whisper|faster_whisper|ctranslate2|torch|torchaudio)\b"
)

BASE_DEPENDENCIES_FORBIDDEN = {
    "faster-whisper",
    "torch",
    "torchaudio",
    "sherpa-onnx",
    "openai-whisper",
    "ctranslate2",
}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src" / "evoblue_video_mcp"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


def _under_providers(path: Path) -> bool:
    rel = path.relative_to(SRC_ROOT)
    return len(rel.parts) >= 2 and rel.parts[0] == "asr" and rel.parts[1] == "providers"


def test_no_asr_engine_imports_outside_providers() -> None:
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if _under_providers(path):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if "evoblue_video_mcp.asr.providers" in stripped:
                continue
            static = stripped.startswith(("import ", "from ")) and ENGINE_TOKENS.search(stripped)
            dynamic = "importlib.import_module" in stripped and ENGINE_TOKENS.search(stripped)
            if static or dynamic:
                offenders.append(f"{path.relative_to(SRC_ROOT)}: {stripped}")
    assert not offenders, "ASR engine imports leaked outside asr/providers:\n" + "\n".join(
        offenders
    )


def test_base_dependencies_exclude_asr_engines() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    base = data["project"]["dependencies"]
    leaked = [
        dep for dep in base if any(name in dep.lower() for name in BASE_DEPENDENCIES_FORBIDDEN)
    ]
    assert not leaked, f"base dependencies must not ship ASR engines: {leaked}"
