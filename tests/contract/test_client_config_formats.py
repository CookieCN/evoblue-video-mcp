"""P4-006: rendered client config fragments ↔ docs/CLIENT_COMPATIBILITY.md.

The doc's samples are the contract; the renderer (scripts/verify_p4_clients.py)
must reproduce them byte-for-byte for the documented command.
"""

import importlib.util
import re
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "CLIENT_COMPATIBILITY.md"
_SCRIPT = _ROOT / "scripts" / "verify_p4_clients.py"

_DOC_COMMAND = r"C:\path\to\.venv\Scripts\python.exe"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("verify_p4_clients", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SAMPLES_HEADING = "### 四客户端配置样例"


def _doc_block(fence: str) -> str:
    doc = _DOC.read_text(encoding="utf-8")
    section = doc.split(_SAMPLES_HEADING, 1)[1]
    pattern = rf"```{fence}\n(.*?)```"
    match = re.search(pattern, section, re.S)
    assert match is not None, f"fence {fence} not found after samples heading"
    return match.group(1)


def test_rendered_codex_fragment_matches_doc_sample() -> None:
    module = _module()
    rendered = module.render_client_config("codex", command=_DOC_COMMAND)
    assert rendered == _doc_block("toml")


def test_rendered_claude_fragment_matches_doc_sample() -> None:
    module = _module()
    rendered = module.render_client_config("claude", command=_DOC_COMMAND)
    assert rendered == _doc_block("json")


def test_deepseek_and_workbuddy_fragments_are_valid_json() -> None:
    import json

    module = _module()
    for client in ("deepseek", "workbuddy"):
        fragment = json.loads(module.render_client_config(client, command=_DOC_COMMAND))
        entry = fragment["mcpServers"]["evoblue-video"]
        assert entry["args"] == ["-m", "evoblue_video_mcp.mcp"]


def test_unknown_client_is_rejected() -> None:
    module = _module()
    try:
        module.render_client_config("unknown")
    except ValueError as exc:
        assert "unknown client" in str(exc)
    else:
        raise AssertionError("expected ValueError")
