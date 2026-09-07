"""P5 contract drift gate: docs/CLIENT_CONFIG_WRITE_CONTRACT.md ↔ code.

The contract document is the source of truth (AGENTS.md work rule: contract
before code). Assertions whose subjects exist at freeze time (registry, status
labels, error codes) import top-level; subjects landing in P5-003 (backup
constants, renderer) and P5-004 (envelope prefix) are imported lazily inside
their tests so each gate turns red individually as its implementation lands —
never as a collection error that masks the gates that already hold.
"""

import re
from pathlib import Path
from typing import get_args

from evoblue_video_mcp.application.client_config.errors import CLIENT_API_ERROR_CODES
from evoblue_video_mcp.application.client_config.models import (
    HANDSHAKE_LABELS,
    EntryPayload,
    HandshakeState,
)
from evoblue_video_mcp.application.client_config.specs import CLIENT_SPECS

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "CLIENT_CONFIG_WRITE_CONTRACT.md"
_COMPAT = _ROOT / "docs" / "CLIENT_COMPATIBILITY.md"

#: The command literal used by every doc sample (documentation placeholder).
_DOC_COMMAND = r"C:\path\to\.venv\Scripts\python.exe"
_DOC_ARGS = ("-m", "evoblue_video_mcp.mcp")


def _doc() -> str:
    return _DOC.read_text(encoding="utf-8")


def _section(doc: str, heading: str) -> str:
    """Body of ``## <heading>`` up to the next top-level ``## `` heading."""
    parts = doc.split(f"## {heading}", 1)
    assert len(parts) == 2, f"contract heading missing: {heading}"
    return parts[1].split("\n## ", 1)[0]


def _table_rows(section_text: str) -> list[list[str]]:
    """All markdown table rows as cell lists, separator row skipped."""
    rows: list[list[str]] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if set(stripped) <= {"|", "-", ":", " "}:
            continue
        rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
    return rows


def test_registry_table_matches_client_specs() -> None:
    rows = _table_rows(_section(_doc(), "1. 客户端注册表（冻结）"))
    assert rows[0] == [
        "client_id",
        "显示名",
        "tier",
        "条目 key",
        "容器",
        "写入目标",
        "备注",
    ]
    data = rows[1:]
    assert [row[0] for row in data] == [spec.client_id for spec in CLIENT_SPECS]
    for row, spec in zip(data, CLIENT_SPECS, strict=True):
        assert row == [
            spec.client_id,
            spec.display_name,
            spec.tier,
            spec.entry_key,
            spec.container,
            spec.write_target or "无",
            spec.note,
        ]


def test_status_enum_and_display_labels_match_contract() -> None:
    rows = _table_rows(_section(_doc(), "6. 验证门禁与状态字符串（冻结）"))
    assert rows[0] == ["状态", "展示标签", "语义"]
    data = rows[1:]
    assert [row[0] for row in data] == list(get_args(HandshakeState))
    labels = {row[0]: row[1] for row in data}
    assert labels == HANDSHAKE_LABELS
    # The PRD acceptance wording is frozen: an unconfirmed automation outcome
    # renders exactly 「未验证」 (docs/CLIENT_COMPATIBILITY.md 共同行为).
    assert labels["unverified"] == "未验证"


def test_error_code_table_matches_frozen_codes_in_order() -> None:
    section = _section(_doc(), "7. HTTP API")
    marker = "| HTTP | code |"
    assert marker in section, "error code table missing from §7"
    rows = _table_rows(section.split(marker, 1)[1])
    data = [row for row in rows if row[0] != "HTTP"]
    codes = [row[1] for row in data]
    assert codes == list(CLIENT_API_ERROR_CODES)
    status_by_code = {row[1]: int(row[0]) for row in data}
    assert status_by_code["CLIENT_UNKNOWN"] == 404
    assert status_by_code["BACKUP_NOT_FOUND"] == 404
    assert status_by_code["ENTRY_MODIFIED"] == 409
    assert status_by_code["OPERATION_IN_PROGRESS"] == 409
    assert status_by_code["AUTO_INSTALL_UNSUPPORTED"] == 409
    assert status_by_code["CONFIRMATION_REQUIRED"] == 422
    assert status_by_code["INVALID_REQUEST"] == 422
    assert status_by_code["CONFIG_WRITE_FAILED"] == 503
    assert status_by_code["CONFIG_UNSUPPORTED"] == 503


def test_route_table_covers_the_frozen_surface() -> None:
    section = _section(_doc(), "7. HTTP API")
    routes = re.findall(r"^\| (?:GET|POST) \| (\S+) \|", section, re.M)
    assert routes == [
        "/api/mcp-clients",
        "/api/mcp-clients/{client_id}/install",
        "/api/mcp-clients/{client_id}/verify",
        "/api/mcp-clients/{client_id}/remove",
        "/api/mcp-clients/{client_id}/backups",
        "/api/mcp-clients/{client_id}/restore",
        "/api/mcp-clients/{client_id}/config",
    ]


def test_backup_constants_match_contract() -> None:
    from evoblue_video_mcp.application.client_config.backup import (
        ABSENT_SENTINEL,
        BACKUP_RETENTION,
        BACKUP_SUFFIX,
    )

    rows = _table_rows(_section(_doc(), "5. 备份与恢复"))
    values = {row[0]: row[1] for row in rows[1:]}
    assert values["BACKUP_SUFFIX"] == BACKUP_SUFFIX
    assert values["BACKUP_RETENTION"] == str(BACKUP_RETENTION)
    assert values["ABSENT_SENTINEL"] == ABSENT_SENTINEL


def _sample_fences() -> list[str]:
    """The three sample fences of §9 in order: toml, compact json, indent json."""
    section = _section(_doc(), "9. 渲染样例（字节锁定）")
    fences = re.findall(r"```(?:toml|json)\n(.*?)```", section, re.S)
    assert len(fences) == 3, f"expected 3 sample fences, found {len(fences)}"
    return fences


def test_rendered_samples_match_contract_fences() -> None:
    from evoblue_video_mcp.application.client_config.render import (
        render_json_fragment,
        render_toml_entry,
    )

    payload = EntryPayload(command=_DOC_COMMAND, args=_DOC_ARGS)
    toml_fence, compact_fence, indent_fence = _sample_fences()
    assert render_toml_entry("evoblue-video", payload) == toml_fence
    assert render_json_fragment("evoblue-video", payload, indent=None) == compact_fence
    assert render_json_fragment("evoblue-video-mcp", payload, indent=2) == indent_fence


def test_contract_samples_agree_with_client_compatibility_samples() -> None:
    """§9 must never drift from the P4-frozen samples in CLIENT_COMPATIBILITY."""
    compat = _COMPAT.read_text(encoding="utf-8")
    section = compat.split("### 四客户端配置样例", 1)[1]
    toml_sample = re.search(r"```toml\n(.*?)```", section, re.S)
    json_sample = re.search(r"```json\n(.*?)```", section, re.S)
    assert toml_sample is not None and json_sample is not None
    toml_fence, compact_fence, _indent_fence = _sample_fences()
    assert toml_fence == toml_sample.group(1)
    assert compact_fence == json_sample.group(1)


def test_mcp_clients_prefix_joins_structured_envelope() -> None:
    from evoblue_video_mcp.web.app import _P3_ENVELOPE_PREFIXES

    assert "/api/mcp-clients" in _P3_ENVELOPE_PREFIXES
