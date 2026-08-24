# EvoBlue Video MCP

EvoBlue Video MCP 是一个 local-first、开源的视频理解与知识管理工具。用户通过本地 WebUI 或 MCP 客户端提交视频链接，Local Engine 异步生成标准 Markdown 报告并提供本地搜索与管理。

## 当前状态

P0 架构与合同骨架。视频下载、字幕、ASR、LLM、持久 Worker 和完整 WebUI 尚未实现，不应把当前仓库当作可交付产品使用。

## 核心架构

```text
AI Client
  -> STDIO MCP Bridge
  -> Local Engine HTTP API (127.0.0.1:8765)
  -> Persistent Job Worker
  -> Video Pipeline
  -> SQLite index + user-owned Markdown
```

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

项目边界见 `docs/SYSTEM_BOUNDARIES.md`，路线图见 `docs/PRD.md`。

