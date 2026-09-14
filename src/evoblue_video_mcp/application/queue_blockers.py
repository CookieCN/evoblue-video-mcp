"""Why is a job not moving? Derived from the same gates the worker applies.

F3 (feedback #3/#9): MCP clients saw "已排队, 等待处理" with no root cause —
the WebUI banner knew about the setup gate, the status tool did not. This
module mirrors ``ProductionHandlerFactory``'s claim gate (settings row +
bounded keyring existence — never a network probe) and synthesizes the full
user-facing message server-side, so bridges only relay it: the URL is the
engine's real address and never carries a token.
"""

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp.config import CredentialStore
from evoblue_video_mcp.storage.repository import get_app_settings

_KEYRING_TIMEOUT_S = 5.0


async def _stored_key_exists(
    credential_store: CredentialStore | None, reference: str | None
) -> bool | None:
    """Bounded keyring existence check; ``None`` means "unknown" (store error)."""
    if credential_store is None or not reference:
        return False
    try:
        value = await asyncio.wait_for(
            asyncio.to_thread(credential_store.get_secret, reference),
            timeout=_KEYRING_TIMEOUT_S,
        )
        return bool(value)
    except Exception:
        return None


async def job_blocker(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    status: str,
    asr_recommendation_model_id: str | None,
    credential_store: CredentialStore | None,
    engine_port: int,
) -> dict[str, Any] | None:
    """Return ``{"reason", "message"}`` for a non-moving job, else ``None``.

    Reasons are stable strings (contract: MCP_TOOLS §6): ``setup_incomplete``,
    ``llm_key_unavailable``, ``keyring_error``, ``waiting_for_model``.
    """
    if status == "waiting_for_model":
        model = asr_recommendation_model_id or "推荐的 ASR 模型"
        return {
            "reason": "waiting_for_model",
            "message": (
                "等待本地语音模型就绪: 到 WebUI 模型页安装 "
                f"{model} (http://127.0.0.1:{engine_port}/models), 安装完成后任务自动继续"
            ),
        }
    if status != "queued":
        return None

    async with session_factory() as session:
        row = await get_app_settings(session)
    if row is None or not row.setup_completed or not (
        row.llm_provider and row.llm_base_url and row.llm_model and row.llm_credential_ref
    ):
        return {
            "reason": "setup_incomplete",
            "message": (
                "任务在排队但不会开始: 请先在 WebUI 完成初始设置 "
                f"(http://127.0.0.1:{engine_port}/settings), 保存后任务自动开始"
            ),
        }
    key = await _stored_key_exists(credential_store, row.llm_credential_ref)
    if key is None:
        return {
            "reason": "keyring_error",
            "message": "任务在排队但不会开始: 系统凭据库暂时不可读, 请稍后重试或重启 EvoBlue",
        }
    if key is False:
        return {
            "reason": "llm_key_unavailable",
            "message": (
                "任务在排队但不会开始: API Key 未配置, 请到 WebUI 设置页补齐 "
                f"(http://127.0.0.1:{engine_port}/settings)"
            ),
        }
    return None
