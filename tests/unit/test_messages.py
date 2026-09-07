"""P4-004: Bridge-side Chinese message synthesis (status / cancel)."""

from evoblue_video_mcp.mcp.messages import cancel_message, status_message


def test_status_messages_cover_running_and_terminal_states() -> None:
    assert status_message("queued") == "已排队, 等待处理"
    assert status_message("summarizing_chunks") == "正在整理视频内容"
    assert status_message("completed") == "分析完成"
    assert status_message("cancelled") == "任务已取消"


def test_failed_status_appends_stable_error_code() -> None:
    assert status_message("failed", error_code="ENGINE_TIMEOUT") == "处理失败 (ENGINE_TIMEOUT)"
    assert status_message("failed") == "处理失败"


def test_unknown_status_degrades_gracefully() -> None:
    assert status_message("mystery_state") == "当前状态: mystery_state"


def test_cancel_message_semantics() -> None:
    assert cancel_message("cancelled", accepted=True) == "任务已取消"
    assert cancel_message("summarizing_chunks", accepted=True) == "已请求取消, 将在安全点生效"
    assert cancel_message("completed", accepted=False) == "任务已是终态 (completed), 无需取消"
    assert cancel_message("failed", accepted=False) == "任务已是终态 (failed), 无需取消"
