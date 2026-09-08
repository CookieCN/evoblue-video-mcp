"""P7 contract drift gate: docs/INSTALLER_RELEASE_CONTRACT.md ↔ code.

Same discipline as the P5 gates: subjects that exist at freeze time (contract
tables, naming helpers) import top-level; subjects landing in P7-002/003/004/
005/006 (singleton constants, __main__ rules, frontend keys, installer literals)
are read lazily inside their tests so each gate turns red individually as its
implementation lands — never as a collection error masking earlier gates.
"""

import importlib
from pathlib import Path

from evoblue_video_mcp.versioning import npm_version, setup_exe_name, win_zip_name

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "INSTALLER_RELEASE_CONTRACT.md"
_ISS = _ROOT / "packaging" / "installer.iss"
_MAIN = _ROOT / "src" / "evoblue_video_mcp" / "__main__.py"
_APP = _ROOT / "frontend" / "src" / "App.jsx"


def _doc() -> str:
    return _DOC.read_text(encoding="utf-8")


def _section(doc: str, heading: str) -> str:
    parts = doc.split(f"## {heading}", 1)
    assert len(parts) == 2, f"contract heading missing: {heading}"
    return parts[1].split("\n## ", 1)[0]


def _table_rows(section_text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if set(stripped) <= {"|", "-", ":", " "}:
            continue
        rows.append([cell.strip().strip("`") for cell in stripped.strip("|").split("|")])
    return rows


def _import(module: str, attribute: str):
    try:
        return getattr(importlib.import_module(module), attribute)
    except (ImportError, AttributeError) as exc:  # pragma: no cover - gate text
        raise AssertionError(
            f"P7 contract subject not implemented yet: {module}.{attribute}"
        ) from exc


# §1 — versioning helpers must reproduce the frozen artifact names.


def test_npm_version_mapping() -> None:
    assert npm_version("0.9.0b1") == "0.9.0-beta.1"
    assert npm_version("1.0.0") == "1.0.0"


def test_artifact_names_derive_from_version() -> None:
    assert setup_exe_name("0.9.0b1") == "EvoBlueVideoMCP-0.9.0-beta.1-setup.exe"
    assert win_zip_name("0.9.0b1") == "evoblue-video-mcp-0.9.0-beta.1-win-full.zip"


def test_contract_artifact_table_names_helpers() -> None:
    rows = _table_rows(_section(_doc(), "1. 版本与产物命名（冻结）"))
    cells = {row[0] for row in rows[1:]}
    assert "EvoBlueVideoMCP-<npmver>-setup.exe" in cells
    assert "evoblue-video-mcp-<npmver>-win-full.zip" in cells
    assert "<npmver>" in _doc()
    assert "npm 归一化" in _doc()


# §2 — installer literals.


def test_app_id_literal_is_frozen_in_iss() -> None:
    app_id = "{55355472-adb9-4258-b2fe-34c2af302239}"
    assert app_id in _doc(), "contract §2 must carry the AppId literal"
    iss = _ISS.read_text(encoding="utf-8")
    assert app_id in iss, "installer.iss must carry the exact frozen AppId"
    assert "PrivilegesRequired=lowest" in iss


def test_autostart_value_name_matches_contract() -> None:
    run_key = "Software\\Microsoft\\Windows\\CurrentVersion\\Run"
    value_name = '"EvoBlue Video MCP"'
    assert run_key.replace("\\\\", "\\") in _doc()
    iss = _ISS.read_text(encoding="utf-8")
    assert run_key.replace("\\\\", "\\") in iss
    assert value_name in iss
    assert "uninsdeletevalue" in iss


def test_silent_uninstall_can_suppress_custom_data_prompts() -> None:
    iss = _ISS.read_text(encoding="utf-8")
    assert "SuppressibleMsgBox(" in iss
    assert "MB_YESNO or MB_DEFBUTTON2, IDNO" in iss
    assert "if MsgBox(" not in iss


# §3 — single-instance: exit codes, mutex, lock payload.


def test_exit_code_table_matches_singleton_constants() -> None:
    rows = _table_rows(_section(_doc(), "3. 单实例合同（冻结）"))
    by_code = {row[0]: row for row in rows[1:]}
    already = _import("evoblue_video_mcp.runtime.singleton", "EXIT_ALREADY_RUNNING")
    port_taken = _import("evoblue_video_mcp.runtime.singleton", "EXIT_PORT_UNAVAILABLE")
    assert str(already) == "3"
    assert str(port_taken) == "4"
    assert by_code["3"][1] == "已有 Engine 实例在运行"
    assert by_code["4"][1] == "端口被其他程序占用"
    doc = _doc()
    assert "EvoBlue Engine 已在运行" in doc
    assert "已被其他程序占用" in doc


def test_mutex_name_is_plain_literal() -> None:
    mutex = _import("evoblue_video_mcp.runtime.singleton", "MUTEX_NAME")
    assert mutex == "EvoBlueVideoMCP-Engine"
    doc = _doc()
    assert '"EvoBlueVideoMCP-Engine"' in doc, "contract §3 must freeze the literal"
    assert "无命名空间前缀" in doc


def test_lock_file_payload_keys() -> None:
    lock_name = _import("evoblue_video_mcp.runtime.singleton", "LOCK_FILENAME")
    keys = _import("evoblue_video_mcp.runtime.singleton", "LOCK_PAYLOAD_KEYS")
    assert lock_name == "engine.lock"
    assert tuple(keys) == ("pid", "port", "started_at", "version")
    assert '{"pid": int, "port": int, "started_at": float, "version": str}' in _doc()


# §4 — WebUI production keys.


def test_token_bootstrap_keys_in_frontend_and_contract() -> None:
    rows = _table_rows(_section(_doc(), "4. WebUI 生产化（冻结）"))
    by_key = {row[0]: row[1] for row in rows[1:]}
    assert by_key["URL fragment 键"].startswith("evoblue_token")
    assert by_key["localStorage 键"].startswith("evoblue.local_token")
    app = _APP.read_text(encoding="utf-8")
    assert "evoblue_token" in app, "App.jsx must read the frozen fragment key"
    assert "evoblue.local_token" in app, "App.jsx must use the frozen localStorage key"
    assert "X-Local-Token" in app, "apiFetch must attach the frozen header"


def test_frozen_production_rule_in_entrypoint() -> None:
    assert "frozen ⇒ production 规则" in _doc()
    main_src = _MAIN.read_text(encoding="utf-8")
    assert "EVOBLUE_ENVIRONMENT" in main_src
    assert "EVOBLUE_OPEN_UI" in main_src
    assert "evoblue_token" in main_src, "engine must open the UI with the fragment"


# §5 — packaged bridge entry.


def test_packaged_bridge_entry() -> None:
    args = _import(
        "evoblue_video_mcp.application.client_config.service", "FROZEN_BRIDGE_ARGS"
    )
    assert tuple(args) == ("bridge",)
    doc = _section(_doc(), "5. 打包 Bridge 入口（冻结）")
    assert "bridge" in doc
    assert "ENGINE_NOT_READY" in doc


# §6 — migration backup naming.


def test_backup_filename_template() -> None:
    template = _import("evoblue_video_mcp.storage.backup", "BACKUP_NAME_TEMPLATE")
    assert template == "evoblue.db.bak-v{version}"
    assert "evoblue.db.bak-v<当前版本>" in _doc()
    assert "MIGRATION_BACKUP_FAILED" in _doc()
