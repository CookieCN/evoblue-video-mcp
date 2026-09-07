"""Frozen client registry — one :class:`ClientSpec` per contract §1 table row.

Order matches the contract table; the drift test parses the table and compares
it to this tuple field by field. New clients enter the contract table first,
then here (AGENTS.md work rule: contract before code).
"""

from evoblue_video_mcp.application.client_config.models import ClientSpec

CLIENT_SPECS: tuple[ClientSpec, ...] = (
    ClientSpec(
        client_id="codex",
        display_name="Codex",
        tier="file_auto",
        entry_key="evoblue-video",
        container="toml",
        write_target="~/.codex/config.toml",
        note="",
    ),
    ClientSpec(
        client_id="claude_desktop",
        display_name="Claude Desktop",
        tier="file_auto",
        entry_key="evoblue-video",
        container="json",
        write_target="%APPDATA%/Claude/claude_desktop_config.json",
        note="仅 Windows 实测路径，POSIX 不支持自动写",
    ),
    ClientSpec(
        client_id="workbuddy",
        display_name="WorkBuddy",
        tier="file_auto",
        entry_key="evoblue-video-mcp",
        container="json",
        write_target="~/.workbuddy/mcp.json",
        note="安装后需在 WorkBuddy 连接器管理页手动 Trust 才激活",
    ),
    ClientSpec(
        client_id="claude_code",
        display_name="Claude Code",
        tier="cli",
        entry_key="evoblue-video",
        container="cli",
        write_target="claude mcp（local 作用域）",
        note="CLI 不在 PATH 时该次调用降级为可复制配置，绝不直接改写 CLI 管理的文件",
    ),
    ClientSpec(
        client_id="deepseek",
        display_name="DeepSeek Harness",
        tier="manual",
        entry_key="evoblue-video",
        container="json",
        write_target=None,
        note="格式仍在变化，只生成可复制配置，永不自动写入",
    ),
)

_SPEC_BY_ID: dict[str, ClientSpec] = {spec.client_id: spec for spec in CLIENT_SPECS}


def get_spec(client_id: str) -> ClientSpec | None:
    return _SPEC_BY_ID.get(client_id)
