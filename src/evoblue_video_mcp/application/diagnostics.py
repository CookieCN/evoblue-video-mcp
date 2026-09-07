"""Engine-side environment diagnostics backing ``GET /api/diagnostics``.

Contract: ``docs/MCP_TOOLS.md`` §7 — check names are the frozen ``CHECK_NAMES``
order, a single failing check degrades only itself (never the whole report),
and all output is redacted: paths shrink to their tail, credentials surface as
「已配置/未配置」only, failures carry exception *type names* — never messages
with absolute paths. Engine libraries are probed via ``importlib.metadata``
(no engine imports outside ``asr/providers/``; only ``onnxruntime`` is imported
lazily inside its check, which the engine-import guard does not scan for).
"""

import asyncio
import importlib.metadata
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoblue_video_mcp import __version__
from evoblue_video_mcp.asr import registry
from evoblue_video_mcp.asr.service import ModelManagerService
from evoblue_video_mcp.config import CredentialStore, llm_credential_reference
from evoblue_video_mcp.storage import SCHEMA_VERSION
from evoblue_video_mcp.storage.rebuild import (
    ReportPointerUnavailable,
    resolve_report_root,
    root_is_usable,
)
from evoblue_video_mcp.storage.repository import get_app_settings

CheckStatus = Literal["pass", "warning", "fail", "skipped"]

#: Frozen check names and output order (mirrors docs/MCP_TOOLS.md §7).
CHECK_NAMES: tuple[str, ...] = (
    "local_engine",
    "engine_version",
    "database",
    "report_directory",
    "disk_space",
    "ffmpeg",
    "yt_dlp",
    "llm_config",
    "llm_api",
    "asr_runtime",
    "gpu",
    "asr_models",
    "cookie_browser",
)

_CHECK_TIMEOUT_S = 4.0
_LLM_PROBE_TIMEOUT_S = 10.0
_FFMPEG_TIMEOUT_S = 3.0
_DISK_WARN_FREE_BYTES = 5 * 1024**3
_DISK_FAIL_FREE_BYTES = 1 * 1024**3


@dataclass(frozen=True)
class DiagnosticOutcome:
    """One redacted check result; ``detail`` never carries secrets or paths."""

    name: str
    status: CheckStatus
    message: str
    detail: str | None = None


@dataclass(frozen=True)
class DiagnosticsReport:
    engine_version: str
    overall: Literal["pass", "warning", "fail"]
    checks: tuple[DiagnosticOutcome, ...]


def redact_path(path: Path | str | None) -> str | None:
    """Reduce a path to its last two segments — never the absolute location."""
    if path is None:
        return None
    parts = Path(path).parts
    if not parts:
        return None
    return ".../" + "/".join(parts[-2:])


def _dist_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


async def _probe_ffmpeg() -> str | None:
    """Return the first ``ffmpeg -version`` output line, or None if absent/broken."""
    if shutil.which("ffmpeg") is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, NotImplementedError):
        # NotImplementedError: selector event loops cannot spawn subprocesses.
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_FFMPEG_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    first = stdout.decode("utf-8", errors="replace").splitlines()
    return first[0].strip() if first else None


async def _guarded(
    name: str, check: Callable[[], Awaitable[DiagnosticOutcome]]
) -> DiagnosticOutcome:
    """Run one check so any failure degrades only that check (MCP_TOOLS §7)."""
    try:
        return await asyncio.wait_for(check(), timeout=_CHECK_TIMEOUT_S)
    except TimeoutError:
        return DiagnosticOutcome(name=name, status="fail", message="检查超时")
    except Exception as exc:  # boundary: type name only, never the message
        return DiagnosticOutcome(
            name=name, status="fail", message="检查失败", detail=type(exc).__name__
        )


async def collect_diagnostics(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    credential_store: CredentialStore | None = None,
    model_service: ModelManagerService | None = None,
    fallback_report_root: Path | None = None,
    pointer_file: Path | None = None,
    data_directory: Path | None = None,
    include_network: bool = False,
    http_get: Callable[[str], Awaitable[int]] | None = None,
    ffmpeg_version_probe: Callable[[], Awaitable[str | None]] | None = None,
) -> DiagnosticsReport:
    """Run every frozen check concurrently; single failures degrade only themselves.

    Concurrent execution with per-check timeouts keeps the local group inside
    the frozen 15 s budget and the network-enabled run inside 30 s
    (MCP_TOOLS.md §0.3). ``http_get`` must return the HTTP status code and is
    injected by the endpoint so tests never touch the network.
    """

    async def check_local_engine() -> DiagnosticOutcome:
        return DiagnosticOutcome(
            name="local_engine", status="pass", message="Local Engine 在线"
        )

    async def check_engine_version() -> DiagnosticOutcome:
        return DiagnosticOutcome(
            name="engine_version",
            status="pass",
            message="Engine 版本",
            detail=__version__,
        )

    async def check_database() -> DiagnosticOutcome:
        async with session_factory() as sess:
            await sess.execute(text("SELECT 1"))
        return DiagnosticOutcome(
            name="database",
            status="pass",
            message="数据库可访问",
            detail=f"schema v{SCHEMA_VERSION}",
        )

    async def check_report_directory() -> DiagnosticOutcome:
        try:
            root = await resolve_report_root(
                session_factory,
                fallback_report_root=fallback_report_root,
                pointer_file=pointer_file,
            )
        except ReportPointerUnavailable:
            return DiagnosticOutcome(
                name="report_directory",
                status="fail",
                message="报告目录指针不可读, 请在 WebUI 重新保存报告目录",
            )
        if root is None:
            return DiagnosticOutcome(
                name="report_directory",
                status="warning",
                message="未配置报告目录",
            )
        if not root_is_usable(root):
            return DiagnosticOutcome(
                name="report_directory",
                status="warning",
                message="报告目录不存在或无法枚举",
                detail=redact_path(root),
            )
        if not os.access(root, os.W_OK):
            return DiagnosticOutcome(
                name="report_directory",
                status="warning",
                message="报告目录不可写",
                detail=redact_path(root),
            )
        return DiagnosticOutcome(
            name="report_directory",
            status="pass",
            message="报告目录可用",
            detail=redact_path(root),
        )

    async def check_disk_space() -> DiagnosticOutcome:
        target = data_directory or fallback_report_root
        if target is None:
            return DiagnosticOutcome(
                name="disk_space", status="skipped", message="无数据目录可检查"
            )
        usage = await asyncio.to_thread(shutil.disk_usage, target)
        free_gib = usage.free / 1024**3
        rounded = f"{free_gib:.1f}"
        if usage.free < _DISK_FAIL_FREE_BYTES:
            return DiagnosticOutcome(
                name="disk_space", status="fail", message=f"磁盘可用不足 1 GB (当前 {rounded} GB)"
            )
        if usage.free < _DISK_WARN_FREE_BYTES:
            return DiagnosticOutcome(
                name="disk_space", status="warning", message=f"磁盘可用偏低 ({rounded} GB)"
            )
        return DiagnosticOutcome(
            name="disk_space", status="pass", message=f"磁盘可用 {rounded} GB"
        )

    async def check_ffmpeg() -> DiagnosticOutcome:
        probe = ffmpeg_version_probe or _probe_ffmpeg
        version_line = await probe()
        if version_line is None:
            return DiagnosticOutcome(
                name="ffmpeg",
                status="warning",
                message="未找到 FFmpeg, 字幕模式不受影响, 部分音频转写场景需要",
            )
        return DiagnosticOutcome(
            name="ffmpeg", status="pass", message="FFmpeg 可用", detail=version_line
        )

    async def check_yt_dlp() -> DiagnosticOutcome:
        version = _dist_version("yt-dlp")
        if version is None:
            return DiagnosticOutcome(
                name="yt_dlp", status="fail", message="yt-dlp 不可用, 请重新安装"
            )
        return DiagnosticOutcome(
            name="yt_dlp", status="pass", message=f"yt-dlp {version}"
        )

    async def check_llm_config() -> DiagnosticOutcome:
        async with session_factory() as sess:
            row = await get_app_settings(sess)
        if row is None or not row.setup_completed:
            return DiagnosticOutcome(
                name="llm_config", status="warning", message="尚未完成初始设置"
            )
        if not (row.llm_provider and row.llm_base_url and row.llm_model):
            return DiagnosticOutcome(
                name="llm_config", status="warning", message="LLM 配置不完整"
            )
        reference = row.llm_credential_ref or llm_credential_reference(row.llm_provider)
        configured = False
        if credential_store is not None:
            try:
                configured = bool(
                    await asyncio.to_thread(credential_store.get_secret, reference)
                )
            except Exception:  # keyring failure counts as "unknown"
                configured = False
        if not configured:
            return DiagnosticOutcome(
                name="llm_config",
                status="warning",
                message=f"LLM 已配置 ({row.llm_provider} / {row.llm_model}), API Key 未配置",
            )
        return DiagnosticOutcome(
            name="llm_config",
            status="pass",
            message=f"LLM 已配置 ({row.llm_provider} / {row.llm_model}, API Key 已配置)",
        )

    async def check_llm_api() -> DiagnosticOutcome:
        if not include_network:
            return DiagnosticOutcome(
                name="llm_api", status="skipped", message="未请求网络诊断"
            )
        async with session_factory() as sess:
            row = await get_app_settings(sess)
        base_url = row.llm_base_url if row else None
        if not base_url or http_get is None:
            return DiagnosticOutcome(
                name="llm_api", status="skipped", message="LLM 未配置, 跳过连通性检查"
            )
        status = await http_get(f"{base_url.rstrip('/')}/models")
        if 200 <= status < 300:
            return DiagnosticOutcome(
                name="llm_api", status="pass", message="LLM API 可达"
            )
        return DiagnosticOutcome(
            name="llm_api",
            status="warning",
            message=f"LLM API 返回 HTTP {status}",
        )

    async def check_asr_runtime() -> DiagnosticOutcome:
        providers = registry.list_providers()
        sherpa = _dist_version("sherpa-onnx")
        ort = _dist_version("onnxruntime")
        runtime_detail = f"sherpa-onnx {sherpa or '未安装'} / onnxruntime {ort or '未安装'}"
        if providers:
            return DiagnosticOutcome(
                name="asr_runtime",
                status="pass",
                message=f"ASR Provider 已注册: {', '.join(providers)}",
                detail=runtime_detail,
            )
        if sherpa or ort:
            return DiagnosticOutcome(
                name="asr_runtime",
                status="warning",
                message="ASR 运行时已安装但当前无可用 Provider",
                detail=runtime_detail,
            )
        return DiagnosticOutcome(
            name="asr_runtime",
            status="warning",
            message="ASR 运行时未安装, 字幕模式不受影响",
        )

    async def check_gpu() -> DiagnosticOutcome:
        if _dist_version("onnxruntime") is None:
            return DiagnosticOutcome(
                name="gpu", status="skipped", message="onnxruntime 未安装"
            )
        import onnxruntime  # type: ignore[import-untyped]

        available = onnxruntime.get_available_providers()
        joined = ", ".join(available) if available else "无"
        return DiagnosticOutcome(
            name="gpu", status="pass", message=f"执行提供者: {joined}"
        )

    async def check_asr_models() -> DiagnosticOutcome:
        if model_service is None:
            return DiagnosticOutcome(
                name="asr_models", status="skipped", message="模型管理未装配"
            )
        models = await model_service.list_models()
        installed = [m for m in models if m.installed]
        if not installed:
            return DiagnosticOutcome(
                name="asr_models",
                status="warning",
                message="尚未安装本地 ASR 模型, 字幕模式不受影响",
            )
        summary = "; ".join(
            f"{m.model_id}@{m.version}" + (" (激活)" if m.active else "")
            for m in installed
        )
        return DiagnosticOutcome(
            name="asr_models",
            status="pass",
            message=f"已安装 {len(installed)} 个模型",
            detail=summary,
        )

    async def check_cookie_browser() -> DiagnosticOutcome:
        return DiagnosticOutcome(
            name="cookie_browser",
            status="warning",
            message="未配置 Cookie 浏览器, 平台字幕模式不需要",
        )

    outcomes = await asyncio.gather(
        _guarded("local_engine", check_local_engine),
        _guarded("engine_version", check_engine_version),
        _guarded("database", check_database),
        _guarded("report_directory", check_report_directory),
        _guarded("disk_space", check_disk_space),
        _guarded("ffmpeg", check_ffmpeg),
        _guarded("yt_dlp", check_yt_dlp),
        _guarded("llm_config", check_llm_config),
        _guarded("llm_api", check_llm_api),
        _guarded("asr_runtime", check_asr_runtime),
        _guarded("gpu", check_gpu),
        _guarded("asr_models", check_asr_models),
        _guarded("cookie_browser", check_cookie_browser),
    )
    by_name = {outcome.name: outcome for outcome in outcomes}
    ordered = tuple(by_name[name] for name in CHECK_NAMES)
    if any(check.status == "fail" for check in ordered):
        overall: Literal["pass", "warning", "fail"] = "fail"
    elif any(check.status == "warning" for check in ordered):
        overall = "warning"
    else:
        overall = "pass"
    return DiagnosticsReport(engine_version=__version__, overall=overall, checks=ordered)
