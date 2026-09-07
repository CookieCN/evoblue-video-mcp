"""Render client config fragments (contract §9 samples are byte-locked).

Pure functions — no filesystem access. The P4 script
(``scripts/verify_p4_clients.py``) delegates here and keeps its old signature
so the P4 byte-lock test keeps passing untouched.
"""

import json

from evoblue_video_mcp.application.client_config.models import EntryPayload

_DEFAULT_ARGS: tuple[str, ...] = ("-m", "evoblue_video_mcp.mcp")


def render_toml_entry(key: str, payload: EntryPayload) -> str:
    """One ``[mcp_servers.<key>]`` block (with optional env subtable).

    TOML basic strings share their escape set with JSON strings for the values
    emitted here, so ``json.dumps`` yields a valid TOML string literal.
    """
    lines = [
        f"[mcp_servers.{key}]",
        f"command = {json.dumps(payload.command, ensure_ascii=False)}",
        f"args = {json.dumps(list(payload.args), ensure_ascii=False)}",
    ]
    if payload.env:
        lines.append("")
        lines.append(f"[mcp_servers.{key}.env]")
        lines.extend(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in payload.env)
    return "\n".join(lines) + "\n"


def render_json_fragment(key: str, payload: EntryPayload, *, indent: int | None) -> str:
    """A ``{"mcpServers": {key: entry}}`` document.

    ``indent=None`` produces the compact single-line form (Claude Desktop
    sample), an int the pretty form (WorkBuddy / DeepSeek samples).
    """
    entry: dict[str, object] = {"command": payload.command, "args": list(payload.args)}
    if payload.env:
        entry["env"] = dict(payload.env)
    fragment: dict[str, object] = {"mcpServers": {key: entry}}
    return json.dumps(fragment, indent=indent, ensure_ascii=False) + "\n"
