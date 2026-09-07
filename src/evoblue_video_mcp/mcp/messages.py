"""Chinese human-readable message synthesis for status and cancel results.

The Engine carries no ``message`` field (ADR 0004): the Bridge derives one
from the job state so every client shows the same localized text. Contract:
``docs/MCP_TOOLS.md`` §2/§6.
"""

from evoblue_video_mcp.jobs import TERMINAL_JOB_STATUSES, JobStatus

_STATUS_MESSAGES: dict[str, str] = {
    JobStatus.QUEUED: "已排队, 等待处理",
    JobStatus.FETCHING_METADATA: "正在获取视频信息",
    JobStatus.FETCHING_SUBTITLES: "正在获取字幕",
    JobStatus.DOWNLOADING_AUDIO: "正在下载音频",
    JobStatus.TRANSCRIBING: "正在转写音频",
    JobStatus.WAITING_FOR_MODEL: "等待本地语音模型就绪",
    JobStatus.CLEANING_TRANSCRIPT: "正在清洗字幕文本",
    JobStatus.CHUNKING: "正在对字幕分块",
    JobStatus.SUMMARIZING_CHUNKS: "正在整理视频内容",
    JobStatus.GENERATING_REPORT: "正在生成报告",
    JobStatus.INDEXING: "正在建立搜索索引",
    JobStatus.RETRY_WAIT: "等待重试",
    JobStatus.COMPLETED: "分析完成",
    JobStatus.FAILED: "处理失败",
    JobStatus.CANCELLED: "任务已取消",
}


def status_message(status: str, *, error_code: str | None = None) -> str:
    """Synthesize the human-readable status line; unknown states degrade."""
    base = _STATUS_MESSAGES.get(status, f"当前状态: {status}")
    if status == JobStatus.FAILED and error_code:
        return f"{base} ({error_code})"
    return base


def cancel_message(status: str, *, accepted: bool) -> str:
    """Synthesize the cancel outcome line (MCP_TOOLS §6 semantics)."""
    if accepted and status == JobStatus.CANCELLED:
        return "任务已取消"
    if accepted:
        return "已请求取消, 将在安全点生效"
    if status in TERMINAL_JOB_STATUSES:
        return f"任务已是终态 ({status}), 无需取消"
    return "取消请求未生效, 请查询任务状态"
