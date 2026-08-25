# EvoBlue Video MCP — Agent Operating Context

## 项目目的

把 YouTube、Bilibili 等视频链接转为用户拥有的标准 Markdown 知识资产：本机抓取元数据与字幕，必要时本地 ASR，经 LLM 总结后保存、搜索和管理。

## 当前阶段

- 初始化日期：2026-08-24
- 当前阶段：P1 — Local Engine、SQLite、Worker、WebUI 框架
- P1 已完成最小闭环：SQLite Job 模型、版本化迁移、单 Worker 租约与崩溃恢复。

## Owner Context

- Owner：Wilson Gu，自媒体博主、跨境营销操盘手。
- 默认中文沟通；代码、命令和变量名使用英文。
- 技术决策必须解释“为什么”和“对用户的影响”。
- 结论先行；发现方案问题直接指出。

## What

输入视频 URL，异步生成字幕、摘要、分析和标准 Markdown；Local Engine 持有状态，WebUI 是配置与管理中心，STDIO MCP Bridge 只做薄适配。

## Business / Product Scope

- Local-first，无远程业务服务器、账户、支付、多租户或云同步。
- 支持 Codex、Claude Desktop、DeepSeek Harness、WorkBuddy。
- WebUI 完成所有首次配置，普通用户无需手改配置文件。
- SQLite 管理任务与搜索索引，Markdown 是最终可迁移资产且可重建索引。
- 首版非目标详见 `docs/PRD.md`。

## Tech Stack

- Python 3.11、FastAPI、Pydantic Settings、SQLAlchemy Async、SQLite/FTS5、MCP Python SDK。
- httpx、yt-dlp、keyring、platformdirs、structlog、tenacity；可选 faster-whisper/CTranslate2/FFmpeg。
- React、Vite、React Router、Tailwind CSS 4、Vitest、ESLint。
- uv、pytest、Ruff、mypy、PyInstaller onedir、GitHub Actions。

## Critical Gotchas

| # | 踩过什么坑 | 规则 |
|---|---|---|
| 1 | 当前沙箱账户与仓库所有者不同，Git 会报告 dubious ownership | 仅对单次 Git 命令使用 `git -c safe.directory=...`，不得修改用户全局配置。 |

## Architecture Decisions

- **Local Engine 是唯一状态所有者**：SQLite、Worker、Pipeline、WebUI 和报告都在 Engine 内，关闭 AI 客户端不会中断已提交任务。
- **STDIO MCP Bridge 是薄适配器**：只校验参数并调用 `127.0.0.1` Engine；不直接碰数据库、下载器、FFmpeg 或 LLM，避免多客户端重复执行与状态分裂。
- **异步任务合同**：提交立即返回 `job_id`，再轮询状态/报告，规避 MCP 客户端约 60 秒工具超时。
- **Markdown 是事实资产**：数据库可丢失并从 Markdown 重建；已完成报告不得只存在 SQLite。
- **故障即安全**：Engine 只绑定 loopback，凭据进入系统凭据库，日志脱敏，子进程禁止 `shell=True`。
- **阶段持久化**：每个 Job 阶段幂等且状态先持久化，重启恢复不依赖仅存在内存中的任务。

## Environment & Commands

```bash
bash init.sh
uv sync --extra dev
uv run ruff check .
uv run mypy src
uv run pytest
cd frontend && npm install && npm run lint && npm run test -- --run && npm run build
```

- 后端健康检查合同：`GET /api/health`。
- Engine 默认端口：`8765`，只允许绑定 `127.0.0.1`；P0 不启动常驻服务。
- 密钥不得写入 `.env`、源码、日志、测试快照或 Markdown 报告。

## Harness Files

| 场景 | 文件 | 位置 |
|---|---|---|
| 项目主入口 | AGENTS.md | 项目根目录 |
| Harness 检查 | init.sh | 项目根目录 |
| 功能清单 | feature_list.json | `.agents/` |
| 项目进度 | progress.md | `.agents/` |
| 变更记录 | CHANGELOG.md | `.agents/` |
| 踩坑经验 | experience.md | `.agents/` |

## Work Rules

- 开工先读 `AGENTS.md`，再按任务读取 `.agents/progress.md`、`.agents/feature_list.json` 和相关合同。
- 先改合同和测试，再实现核心逻辑；旧 EvoBlue 只能作为候选能力来源，不能决定新架构。
- 涉及代码、脚本或系统建设，改完必须运行与风险相称的验证；不得注释掉错误换取通过。
- 所有计划只写阶段、前置依赖与验收标准，不预测天数、周数、人日或完成日期。
- 密钥、token、密码不进代码；日志、诊断和错误返回必须脱敏。
- 不自动 git push，不自动部署，不启动长期运行服务。
- 收工按实际变化更新 feature list → progress → experience（有坑）→ CHANGELOG（重要变化）→ AGENTS（规则/架构变化）。

