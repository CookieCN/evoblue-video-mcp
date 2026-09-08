# ADR 0004: P4 MCP 返回信封与 Bridge 范围

- Status: Accepted
- Date: 2026-09-06

## Context

P4 交付 STDIO MCP Bridge。开工前四组事实约束了设计：

1. **信封分裂**：`docs/MCP_TOOLS.md`（P0 冻结）要求工具返回 `{"ok":...}` 信封，但
   `mcp/schemas.py` 的 20 个传输无关模型全部没有 `ok` 字段，`ToolError` 是孤儿模型；
   progress.md 待做 #2 悬置至今。Engine 侧另有两种 HTTP 信封（旧 `{"detail":...}` 与
   P3 `{"error":{code,message}}`）。
2. **SDK 形态**：锁定 `mcp 2.0.0` 的高层 API 是 `mcp.server.mcpserver.MCPServer`
   （不是 v1 的 FastMCP）。实测 SDK 的 `func_metadata`：工具返回注记为**裸联合**
   （`A | B`）时会落入兜底分支，structuredContent 被包成 `{"result": {...}}`，与合同
   的平铺示例直接冲突；只有单一 BaseModel 子类或 **RootModel** 能保住顶层 schema。
   实测 pydantic：`RootModel[Annotated[Ok | Fail, Field(discriminator="ok")]]` 的
   schema 顶层是 `oneOf`，序列化吐平铺 JSON。
3. **数据模型边界**：`Job` 表没有 `title`/`platform`/`url` 列——给
   `list_analysis_jobs` 做「运行中任务按平台过滤」不是加一个查询参数，而是要动存储
   模型。Engine 无队列上限，`QUEUE_FULL` 目前不可达。合同 §7 的 `CTranslate2` 检查是
   PRD 早期措辞残留，真实 ASR 栈是 sherpa-onnx + onnxruntime。
4. **边界合同**（ADR 0001、SYSTEM_BOUNDARIES）：Bridge 是薄适配器，只调 loopback
   Engine；诊断类检查（FFmpeg、yt-dlp、LLM、模型）必须做在 Engine 侧。

Wilson 决策（2026-09-06）：Engine 离线时 P4 只报 `ENGINE_NOT_READY` + 启动指引；
四客户端中 Codex / Claude Code / WorkBuddy 本机真实验证，DeepSeek 标「未验证」。

## Decision

- **信封所有权在 MCP 层，不在 REST 层**。工具返回统一为：成功 = `ok:true` + 平铺业务
  字段；失败 = `ok:false` + `error{code,message,retryable,detail}`。两者都是工具的
  **正常结果**，MCP `isError` 恒为 `false`——领域失败是工具对问题的正常回答，部分
  客户端对 `isError=true` 只展示文本、丢弃 structuredContent，会丢掉 `code/retryable`。
  **交付形态（2026-09-06 修订，激活预案 A）**：真实 stdio 线路验证发现 mcp 2.0.0 对
  `tools/list` 的 `Tool.outputSchema` 强制要求顶层 `type: object`，而判别联合
  （RootModel oneOf）的 JSON Schema 没有顶层 `type`——带联合 outputSchema 的工具
  在 `tools/list` 直接被协议层拒绝（`Handler returned an invalid result`）。因此
  工具以 `structured_output=False` 注册，信封作为 **JSON 文本内容**交付，
  `structuredContent` 有意缺席；`XxxResult` RootModel 联合退居运行时校验器
  （载荷仍逐字段过冻结模型，违规载荷降级 `BRIDGE_INTERNAL`）。预案 B（失败
  `raise ToolError`）保持否决——它会把领域失败变成 `isError=true`。
- **旧 REST 端点信封不动**（显式修订 ADR 0003「旧端点保持原样到 P4 统一」的话头）：
  MCP 错误信封是 Bridge→客户端的合同，Engine 内部信封不在其约束范围，翻译正是薄适配
  的职责；动旧端点会破坏 P1–P3 已验收的 WebUI 合同与测试断言，回归面大而收益是审美性
  的。触发条件：P7 打包前若做 WebUI v2 再议；新端点（`/api/diagnostics`）从出生就用
  P3 结构化信封。
- **`list_analysis_jobs` 合并逻辑放 Bridge**：`/api/jobs` 与 `/api/history` 是两份已
  冻结的分页合同，不开第三个合并端点；合并 + 投影 + `message` 合成是表现层职责。
  `platform`/`query` 只作用历史源；运行中条目 `title`/`platform` 为 `null`；运行中
  在前、按 `job_id` 去重（运行中胜出）。`status` 过滤下推 `/api/jobs`；历史源视为
  `completed`。
- **`QUEUE_FULL` 标 reserved**：本地单用户无真实队列压力，实现它需要加配置项并改
  submit 语义，收益为零；Bridge 保留其映射位。错误码总表（15 码）是唯一权威清单，
  漂移测试锁定 `STABLE_ERROR_CODES` 与合同表逐行一致。
- **诊断检查名冻结枚举**：`CTranslate2` 更名 `asr_runtime`（真实栈语义），新增
  `engine_version`，Whisper 模型泛化为 `asr_models`；`CheckName` Literal 与合同枚举
  漂移测试锁定。单项检查失败绝不拖垮工具（各自 try/except + 独立超时）。
- **Bridge 不自动拉起 Engine**（SYSTEM_ARCHITECTURE 用词是「可」非「必须」）：自动
  拉起需要单实例锁、生命周期归属与 exe 来源（P7 安装器才定），P4 只留 `ensure_ready()`
  钩子并在离线时返回 `ENGINE_NOT_READY`（retryable）+ 启动指引。单实例约束 = 一个
  端口一个 Engine（绑定失败即退出），锁文件归 P7。
- **stdio 入口**：主形态 `command=<venv>/Scripts/python.exe, args=["-m",
  evoblue_video_mcp.mcp"]`；不用 `uv run` 作主形态（四客户端并发拉起会争 uv 锁并触发
  sync 检查，启动延迟叠加到每次工具调用）；`evoblue-bridge` console script 为辅；
  PyInstaller exe 形态归 P7。token 发现只读（env → `<data_dir>/local_token`），Bridge
  永不生成或写 token 文件；401 → `ENGINE_UNAUTHORIZED` + WebUI 重新配对指引。
- **日志只进 stderr**：Bridge 进程不 import yt_dlp/FFmpeg/LLM 模块（边界合同 +
  stdout 污染双保险）；集成测试全程断言 stdout 每行是合法 JSON-RPC 帧。

## Consequences

- 客户端拿到统一的 `ok` 信封（JSON 文本内容），`code/retryable` 永远可机读；代价有二：
  `isError=true` 的协议级语义在 P4 空置（保留给 SDK 意外），且 `structuredContent`
  缺席——程序化客户端需解析文本 JSON。若未来 SDK 放宽 `outputSchema.type` 约束，
  可恢复联合 schema + structuredContent，载荷形状不变。
- 已实测的传输层事实（对后续阶段有约束力）：SDK 生成的参数模型默认 `extra='ignore'`
  （静默丢未知字段），Bridge 在注册前收紧 `ArgModelBase` 为 `extra='forbid'` 才满足
  合同的「不认识的输入字段默认拒绝」，行为由工具测试锁定；`EVOBLUE_LOCAL_TOKEN`
  环境变量是 `local_access_token` 字段的合同别名（`AliasChoices`，因
  `validation_alias` 不吃 `env_prefix` 而显式列出全名）；Bridge→Engine 的 httpx
  客户端必须 `trust_env=False`，否则本机代理会把「Engine 未启动」翻译成 502、
  掩盖 `ENGINE_NOT_READY`。
- `mcp/schemas.py` 与 `web/schemas.py` 双模型源继续存在，由三方漂移测试
  （合同 md ↔ mcp 模型 ↔ web `_SECTION_HEADINGS`）钉住；收敛到单一模型源留待 REST
  信封统一议题一起处理。
- 运行中任务在 `list_analysis_jobs` 里无标题/平台可显示——已写入合同为显式语义，
  不是缺陷；未来要真过滤，正确做法是 Engine 给 `/api/jobs` 暴露 `url` 并派生 platform。
- 错误翻译矩阵（HTTP 状态/连接错误 → 稳定码）集中在 `mcp/errors.py`，新增 Engine
  错误面时只改一处。

## Amendment (2026-09-07, P8)

- `diagnose_environment` 的检查名冻结枚举按只增不改原则追加 `worker_runtime`
  （Worker 认领门禁镜像：setup_incomplete / llm_key_unavailable / keyring_error
  / ready）。枚举追加对客户端向后兼容；穷举检查名的消费方需同步。
