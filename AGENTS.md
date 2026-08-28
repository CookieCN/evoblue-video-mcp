# EvoBlue Video MCP — Agent Operating Context

## 项目目的

把 YouTube、Bilibili 等视频链接转为用户拥有的标准 Markdown 知识资产：本机抓取元数据与字幕，必要时本地 ASR，经 LLM 总结后保存、搜索和管理。

## 当前阶段

- 初始化日期：2026-08-24
- 当前阶段：P3 — 历史、Markdown、FTS5、索引重建（四合同已冻结，实现待开工；P2 已验收；P6/ASR 的 ASR-0～ASR-4 已全部完成并收口）
- P1/P2 已完成：SQLite Job 模型、版本化迁移、单 Worker 租约、崩溃恢复、字幕分析闭环；P6/ASR 全链路（Provider 分层、Model Manager、路由、基准门禁、打包矩阵与发布验证）完成，Standard 已过审为正式默认模型。

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
- httpx、yt-dlp、keyring、platformdirs、structlog、tenacity；FFmpeg；规划采用 sherpa-onnx + SenseVoiceSmall INT8，whisper.cpp/faster-whisper 仅作可选兼容能力。
- React、Vite、React Router、Tailwind CSS 4、Vitest、ESLint。
- uv、pytest、Ruff、mypy、PyInstaller onedir、GitHub Actions。

## Critical Gotchas

| # | 踩过什么坑 | 规则 |
|---|---|---|
| 1 | 当前沙箱账户与仓库所有者不同，Git 会报告 dubious ownership | 仅对单次 Git 命令使用 `git -c safe.directory=...`，不得修改用户全局配置。 |
| 2 | Windows sandbox 曾把 `.agents` Owner 改为 `CodexSandboxOffline`，导致所有命令在 setup refresh 阶段失败 | 先查 `~/.codex/.sandbox/sandbox.YYYY-MM-DD.log`；只恢复日志点名目录的 Owner，不扩大 ACL、不长期绕过 sandbox。 |
| 3 | Windows System32 可能提供 ONNX Runtime 1.10，导致 sherpa 报 `version [27] ... only 1 to 10` | 这是 C API DLL 冲突，不是模型版本；Provider 必须在 import sherpa 前注册 ASR 环境的 `onnxruntime/capi` DLL 目录，真实模型加载测试不可跳过。 |

## Architecture Decisions

- **Local Engine 是唯一状态所有者**：SQLite、Worker、Pipeline、WebUI 和报告都在 Engine 内，关闭 AI 客户端不会中断已提交任务。
- **STDIO MCP Bridge 是薄适配器**：只校验参数并调用 `127.0.0.1` Engine；不直接碰数据库、下载器、FFmpeg 或 LLM，避免多客户端重复执行与状态分裂。
- **异步任务合同**：提交立即返回 `job_id`，再轮询状态/报告，规避 MCP 客户端约 60 秒工具超时。
- **Markdown 是事实资产**：数据库可丢失并从 Markdown 重建；已完成报告不得只存在 SQLite。
- **故障即安全**：Engine 只绑定 loopback，凭据进入系统凭据库，日志脱敏，子进程禁止 `shell=True`。
- **阶段持久化**：每个 Job 阶段幂等且状态先持久化，重启恢复不依赖仅存在内存中的任务。
- **ASR 是可插拔能力，不是安装包负担**：平台字幕优先；中文采用同一 sherpa-onnx 运行时下的 Lite/Standard 分层，Lite 候选为约 63.4 MB 的离线 Zipformer CTC small INT8，Standard 候选为约 228 MB 的 INT8-only SenseVoiceSmall；其他语言由可选 `whisper.cpp` 包兜底。模型权重不进入基础安装包，由 Local Engine Model Manager 按需、可续传、校验后安装。生产 Manifest 禁止下载同时含 FP32 与 INT8 的 SenseVoice 完整包；每一层只有通过项目基准、许可审查与中国下载链路验证后才能成为正式推荐；执行计划见 `docs/ASR_PLAN.md`。
- **ASR 合同层（ASR-0）**：Provider 协议见 `src/evoblue_video_mcp/asr/base.py`（归一化结果强制不变量 + 取消/进度行为合同），模型 Manifest 校验见 `asr/manifest.py`，基准评分命令 `python -m evoblue_video_mcp.asr.benchmark`。具体引擎只允许在 `asr/providers/` 下 import；核心层通过 Provider 注册表获取引擎（ASR-1），静态扫描 + 基础依赖守卫只作防手滑护栏，不能靠字符串扫描实现严格隔离。
- **ASR-1 已完成**：Lite/Standard 已通过真实模型加载与转写、60 分钟有界单调分段、运行中取消后的 checkpoint/resume 和许可证清单验收；Fake 全绿不得替代真实引擎门禁。
- **模型许可必须落到精确制品**：代码仓库许可证不等于模型权重或转换归档许可证。SenseVoice 制品按 FunASR Model License 1.1 留证；Zipformer 精确归档的使用/再分发声明尚待确认。生产 Manifest 必须区分 `upstream_only`、`mirror_approved` 与 `blocked`；未审批镜像不得成为下载源。
- **ASR-2 模型交付已落地**：`asr/manifests.py` 内置 Lite/Standard 生产 Manifest（`upstream_only`，钉实测逐文件 SHA）；`installer` 多源回退（某源失败切下一源即从零下载，坏 partial 不跨源续传）；`asr/service.py` + `/api/models` + 前端 `/models` 页提供安装/取消/卸载。中国源按「器械+留证、暂不落镜像」交付：hf-mirror 实测可达可续传，但散文件 SHA 与归档不同、Lite 归档无许可证，故不 ship 任何 `mirror_approved` 源（见 `docs/ASR_CHINA_SOURCE_QUALIFICATION.md`）。
- **P3 四合同已冻结（2026-08-28）**：Markdown Schema v1.0 见 `docs/MARKDOWN_SCHEMA.md`（frontmatter 字段表 + 段落解析锚点，`analysis_id ≡ job_id`）；历史/搜索 REST API 见 `docs/HISTORY_SEARCH_API.md`（新端点用 `{"error":{code,message}}` 信封，旧端点 P4 统一）；FTS5 与迁移 v8 见 `docs/FTS5_SCHEMA.md`（自带文本 FTS5 表，`rowid == report_documents.id`，中文检索 = unicode61 + CJK 单字切分，索引与查询同构预处理）；重建行为见 `docs/INDEX_REBUILD.md`（唯一确定性重扫算法，永不写用户 `.md`，幂等重跑即崩溃恢复）。实现必须逐字符转录合同 DDL；迁移文件归 P3 所有，ASR-4 并行拥有 `asr/`、许可证、打包与 CI。
- **ASR-3 路由闭环已落地**：语言感知路由优先复用已安装模型；SenseVoice 覆盖语言不要求 Whisper，其他语言只建议可选 `whisper-cpp-base`。缺模型进入非 claimable 的 `waiting_for_model`，持久化单一安装建议且不隐式下载；恢复走统一 reconciliation（双门禁：SQLite 安装记录 + Provider 已注册；触发点为 Engine 启动、安装完成、保存 whisper CLI 路径），消除安装提交后崩溃导致的永久等待，CLI 后配置也能自动恢复。Silero VAD 是随包受管依赖（识别归档不含 VAD，`asr/vad.py` 钉 SHA 校验，资源访问全程包在故障边界内——缺失/不可读只降级为 Tier 未就绪，绝不阻塞 Engine 启动）；Provider 注册是期望状态对账：CLI 更换/清空与模型文件删除会替换或注销注册（所有权按实例追踪，外部注册不受影响），卸载模型即时注销引擎；Provider 加载失败只让该 Tier 保持未注册（已知坏签名仅事件路径重试），绝不阻塞 Engine 启动，加载失败日志只记 provider_id + 稳定错误码 + 异常类型。Silero MIT 完整许可文本已随包与第三方声明落证。应用级固定 Provider 覆盖自动路由，报告记录实际 provider/model/version。
- **ASR-4 基准门禁与正式默认审批（2026-08-28）**：默认模型身份只能来自 `asr/approvals.py` 的审批注册表，按精确 `(model_id, version)` 记录——模型换版本即失效，必须重跑门禁，不得继承。门禁证据见 `docs/ASR_RELEASE_GATE.md` 与 `benchmarks/results/`：**Standard SenseVoiceSmall INT8 全项通过，是唯一的正式默认**（zh/mixed 自动推荐）；**Lite Zipformer 未过审**（TTS 语料 v1 实体召回 0.50 < 0.60，且归档无许可证文件），保持可安装、可显式选择、绝不自动推荐；whisper-cpp-base 无基准测量，仅是覆盖外语言的路由建议，不是正式默认。基准语料为 TTS 合成（质量下界），换语料或换阈值版本都必须重审。打包矩阵：PyInstaller onedir base（无 ASR 运行时）/ full（含 sherpa+onnxruntime+numpy）双变体，模型权重永不入包，体积增量实测记录于 `docs/RELEASE_VERIFICATION.md`；发布验证以 `scripts/verify_release.py` + 人工清单为准。

## Environment & Commands

```bash
bash init.sh
uv sync --extra dev
uv run ruff check .
uv run mypy src
uv run pytest
cd frontend && npm install && npm run lint && npm run test -- --run && npm run build
```

ASR/发布专用命令：

```bash
uv run python scripts/build_benchmark_corpus.py --out benchmarks/corpus   # 生成基准语料（Windows）
uv run python -m evoblue_video_mcp.asr.benchmark benchmarks/corpus/corpus.json --provider standard --gate
uv run python scripts/build_package.py        # PyInstaller base+full 双变体
uv run python scripts/verify_release.py dist/evoblue-video-mcp-full   # 打包链路验证
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
