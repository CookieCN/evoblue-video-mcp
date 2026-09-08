# EvoBlue Video MCP

EvoBlue Video MCP 是一个 local-first、开源的视频理解与知识管理工具。用户通过本地 WebUI 或 MCP 客户端提交视频链接，Local Engine 异步生成标准 Markdown 报告并提供本地搜索与管理。

## 当前状态

**P1–P4 与 P6 已交付，可真实使用**：提交 YouTube/Bilibili 链接 → 本地 Worker 抓取元数据/字幕 → LLM 生成中文 Markdown 报告 → FTS5 全文可搜、删库可从 Markdown 重建；本地 ASR（SenseVoice Standard 已过审为正式默认模型）覆盖无字幕视频；七个 MCP 工具经 STDIO Bridge 已在 Claude Code、Codex 真实调用通过，WorkBuddy 已完成真实注册、握手与工具发现（工具级任务矩阵待 Trust 后补测）。P5 已交付：WebUI「客户端」页可一键安装/验证/移除/恢复 MCP 客户端配置（Codex、Claude Desktop、WorkBuddy、Claude Code；DeepSeek 提供可复制配置），写入前备份、写入后真实握手验证，详见 `docs/CLIENT_CONFIG_WRITE_CONTRACT.md`。P7 已交付：Windows 用户级安装器 + 便携 zip、Engine 单实例（重复启动给出中文提示）、打包 WebUI 生产化（自动打开浏览器并完成本机配对）、`<引擎exe> bridge` 打包形态 MCP 入口；GitHub Release 由 tag 触发构建并创建草稿，发布动作 Owner 手动完成（`docs/INSTALLER_RELEASE_CONTRACT.md`）。交付中：P8（公开测试加固）。路线图见 `docs/PRD.md`。

## 核心架构

```text
AI Client
  -> STDIO MCP Bridge
  -> Local Engine HTTP API (127.0.0.1:8765)
  -> Persistent Job Worker
  -> Video Pipeline
  -> SQLite index + user-owned Markdown
```

## 安装（Windows 普通用户）

从 GitHub Releases 下载 `EvoBlueVideoMCP-<版本>-setup.exe` 双击安装（用户级，无需管理员）：

- 安装时可勾选「登录时自动启动 EvoBlue Engine」（默认推荐）；
- 安装完成后引擎自动启动并打开浏览器，首次访问会自动完成本机配对；
- 「开始菜单 → EvoBlue Video MCP」随时手动启动；
- 卸载时个人数据（报告、数据库、模型）默认保留，可在卸载确认框中选择清除；
- 被杀软/SmartScreen 拦截时的说明见 `docs/SUPPORT.md`。

偏好免安装的用户下载 `evoblue-video-mcp-<版本>-win-full.zip` 解压即用（运行其中
的 `evoblue-engine-full.exe`）。MCP 客户端在 WebUI「客户端」页一键接入；打包
形态的 Bridge 入口为 `<引擎exe> bridge`。

## 运行（开发者）

```bash
uv sync --extra dev
uv run python -m evoblue_video_mcp   # 启动 Local Engine（127.0.0.1:8765 + WebUI）
```

MCP 客户端接入（Claude Code / Codex / WorkBuddy / DeepSeek）：

```bash
uv run python scripts/verify_p4_clients.py render --client claude   # 生成配置片段
uv run python scripts/verify_p4_clients.py check-handshake          # 真实握手验证
```

配置合同见 `docs/CLIENT_COMPATIBILITY.md`；Engine 未启动时工具返回 `ENGINE_NOT_READY`（Bridge 不自动拉起 Engine，ADR 0004）。

## 开发检查

```bash
bash init.sh
uv sync --extra dev
uv run ruff check .
uv run mypy src
uv run pytest
```

前端：

```bash
cd frontend
npm install
npm run lint
npm run test -- --run
npm run build
```

常见问题与安装疑难见 `docs/SUPPORT.md`；项目边界见 `docs/SYSTEM_BOUNDARIES.md`，路线图见 `docs/PRD.md`。
