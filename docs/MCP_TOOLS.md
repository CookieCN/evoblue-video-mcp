# MCP Tool Contracts

## 通用约定

- Bridge 使用 STDIO，stdout 只输出协议；调用 Local Engine 的超时短于客户端工具超时。
- 提交立即返回，不等待视频处理。时间使用 RFC 3339 UTC，路径使用本机绝对路径但不得包含凭据或无关用户目录。
- 错误统一为：

```json
{
  "ok": false,
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "给用户看的简洁信息",
    "retryable": false,
    "detail": "已脱敏的诊断信息或 null"
  }
}
```

- Tool Schema 版本独立于 Markdown Schema；不认识的输入字段默认拒绝，防止客户端拼写错误被静默忽略。

## 0.1 返回信封（P4 冻结）

每个工具的返回都是一段结构化 JSON，二者选一：

- 成功：`"ok": true` + 平铺业务字段（各工具的「成功」示例即权威形状）。
- 失败：`"ok": false` + `error` 对象（即上文「错误统一为」的结构）。

规则：

- Bridge 把两种返回都作为**正常工具结果**交付（JSON 文本内容，P4 不使用 MCP structuredContent——协议要求 outputSchema 顶层 `type: object`，判别联合 schema 无法满足，见 ADR 0004），MCP `isError` 恒为 `false`。领域失败（Engine 离线、任务不存在）是工具对问题的正常回答；部分客户端对 `isError=true` 只展示文本，会丢失 `code/retryable`。`isError=true` 保留给 SDK/协议层意外（如未知输入字段被拒），P4 工具内不产生。
- 客户端必须先读 `ok` 再读业务字段；`ok` 是成功/失败判别字段。
- `detail` 恒为已脱敏文本或 `null`，绝不包含绝对路径、凭据或 Cookie。

## 0.2 稳定错误码总表（P4 冻结）

| code | retryable | 触发方 | 语义 |
|---|---|---|---|
| `INVALID_URL` | false | Engine | URL 无法解析或未通过规范化校验 |
| `UNSUPPORTED_PLATFORM` | false | Engine | 平台识别成功但不受支持 |
| `ENGINE_NOT_READY` | true | Bridge | Engine 未启动或端口不可达；先启动 Local Engine 再重试 |
| `ENGINE_TIMEOUT` | true | Bridge | Engine 未在工具预算内响应；可重试，提交类重试依赖幂等键 |
| `ENGINE_UNAUTHORIZED` | false | Bridge | 本机 token 校验失败；在 WebUI 重新完成本机配对 |
| `ENGINE_PROTOCOL_ERROR` | true | Bridge | Engine 响应形状不符合合同；属于本地故障 |
| `JOB_NOT_FOUND` | false | Bridge | `job_id` 不存在 |
| `REPORT_NOT_READY` | true | Bridge | 任务尚未完成或报告文件当前不可得 |
| `SECTION_NOT_AVAILABLE` | false | Engine | 该 section 在当前报告中不存在 |
| `REPORT_READ_FAILED` | false | Engine | 报告文件存在但不可读或解析失败 |
| `INVALID_FILTER` | false | Engine | 列表/报告过滤参数非法 |
| `QUERY_INVALID` | false | Engine | 搜索查询为空或语法不可接受 |
| `SEARCH_INDEX_UNAVAILABLE` | true | Engine | 搜索索引缺失或损坏；触发重建后可恢复 |
| `QUEUE_FULL` | — | reserved | 预留码；本地单用户 Engine 无队列上限，当前版本不可达 |
| `BRIDGE_INTERNAL` | false | Bridge | Bridge 内部意外；查看 Bridge/Engine 日志 |

- 本表是错误码的唯一权威清单；新增码必须先改本表，再同步 `STABLE_ERROR_CODES`（合同漂移测试锁定两边一致）。

## 0.3 Bridge 对 Engine 的超时预算（P4 冻结）

| 工具 | 预算（秒） |
|---|---|
| `submit_video_analysis` | 10 |
| `get_analysis_status` | 5 |
| `get_analysis_report` | 10 |
| `list_analysis_jobs` | 10 |
| `search_analysis_history` | 10 |
| `cancel_analysis` | 10 |
| `diagnose_environment`（本地组） | 15 |
| `diagnose_environment`（含网络） | 30 |
| 健康检查 / 版本握手 | 3 |

- 超时一律映射 `ENGINE_TIMEOUT`（retryable=true）；诊断工具按组预算，单项检查内部另有独立超时，单项超时不等于工具超时。

## 1. submit_video_analysis

输入 Schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["url"],
  "properties": {
    "url": {"type": "string", "minLength": 1, "maxLength": 2048},
    "mode": {"type": "string", "enum": ["auto", "standard", "unboxing"], "default": "auto"},
    "asr": {"type": "string", "enum": ["auto", "disabled", "required"], "default": "auto"},
    "language": {"type": ["string", "null"], "maxLength": 64, "default": null}
  }
}
```

成功：

```json
{"ok": true, "job_id": "01J...", "status": "queued", "next_action": "get_analysis_status", "reused": false}
```

- 错误：`INVALID_URL`、`UNSUPPORTED_PLATFORM`、`ENGINE_NOT_READY`、`QUEUE_FULL`、通用错误结构。
- 幂等性：按规范 URL + mode + asr + language + 配置指纹复用近期运行/完成 Job；返回 `reused`。
- 只读：否，会创建或复用任务。
- 超时：Bridge 对 Engine 请求预算 10 秒；超时返回 `ENGINE_TIMEOUT`，不能在未知提交结果时盲目重复创建，重试仍依赖幂等键。
- 兼容：所有客户端应在一次短调用内得到 `job_id`；不要流式等待完成。

## 2. get_analysis_status

输入 Schema：

```json
{"type":"object","additionalProperties":false,"required":["job_id"],"properties":{"job_id":{"type":"string","minLength":1,"maxLength":64}}}
```

成功：

```json
{
  "ok": true,
  "job_id": "01J...",
  "status": "summarizing_chunks",
  "progress": 72,
  "stage": "summarizing_chunks",
  "message": "正在整理视频内容",
  "retryable": false,
  "error_code": null,
  "error_detail": null
}
```

- 错误：`JOB_NOT_FOUND`、`ENGINE_TIMEOUT`。
- 字段语义：`stage` 是 ≤32 字符的管线阶段名（自由字符串，如 `summarizing_chunks`），不是任务状态枚举，可为 `null`；`message` 由 Bridge 按当前状态合成的人类可读文本，Engine 不提供该字段。
- **阻塞原因（F3，2026-09-11，反馈 #3/#9）**：`queued`（初始设置未完成 / Key 缺失 / 凭据库不可读）与
  `waiting_for_model` 状态下，Engine 的 `/api/jobs/{id}` 附带 `blocked_reason`（稳定串：
  `setup_incomplete` / `llm_key_unavailable` / `keyring_error` / `waiting_for_model`）与
  `blocked_message`（Engine 侧合成的完整指引，含**实际 Engine 地址**、绝不携带 token）；
  Bridge 的 `message` 优先透传 `blocked_message`。判定与 Worker 领取门禁同源（settings 行 +
  有界 keyring 存在性），不做网络探测，预算不变。
- 幂等性：相同时间点读取无副作用；状态可能随 Worker 前进。
- 只读：是。
- 超时：5 秒；超时不改变 Job。
- 兼容：`progress` 为 0–100 或 null；客户端应同时显示 `stage/message`，不可只依赖百分比。

## 3. get_analysis_report

输入 Schema：

```json
{
  "type":"object",
  "additionalProperties":false,
  "required":["job_id"],
  "properties":{
    "job_id":{"type":"string","minLength":1,"maxLength":64},
    "section":{"type":"string","enum":["summary","outline","marketing","comments","metadata","transcript","full"],"default":"summary"}
  }
}
```

成功：

```json
{"ok":true,"job_id":"01J...","section":"summary","markdown":"## 核心摘要\n...","file_path":"D:\\Reports\\video.md","schema_version":1,"truncated":false}
```

- 错误：`JOB_NOT_FOUND`、`REPORT_NOT_READY`、`SECTION_NOT_AVAILABLE`、`REPORT_READ_FAILED`。
- 幂等性：只读；文件可能由用户在外部修改，返回当前可用内容。
- 只读：是。
- 超时：10 秒；`transcript/full` 必须应用响应字符上限，超限返回 `truncated: true` 和文件路径，不做长时间传输。
- 兼容：默认 `summary`，避免 MCP 上下文被完整字幕淹没；Windows 路径作为普通字符串返回。

## 4. list_analysis_jobs

输入 Schema：

```json
{
  "type":"object",
  "additionalProperties":false,
  "properties":{
    "limit":{"type":"integer","minimum":1,"maximum":100,"default":20},
    "offset":{"type":"integer","minimum":0,"default":0},
    "status":{"type":["string","null"],"default":null},
    "platform":{"type":["string","null"],"maxLength":64,"default":null},
    "query":{"type":["string","null"],"maxLength":200,"default":null}
  }
}
```

成功：

```json
{"ok":true,"items":[{"job_id":"01J...","title":"...","platform":"youtube","status":"completed","progress":100}],"limit":20,"offset":0,"total":1}
```

- 错误：`INVALID_FILTER`、`ENGINE_TIMEOUT`。
- 数据源合并（P4 冻结；F3 修订 2026-09-11；F4 修订 2026-09-11；R6/R7 三轮修订 2026-09-14）：运行中（非终态）任务与 `failed`/`cancelled` 终态任务来自 `/api/jobs`，已完成历史来自 `/api/history`。默认（`status=null`）排序：运行中条目在前（按提交时间降序，`id` 唯一决胜）→ 任务表中的 `failed`/`cancelled` 行（SQL 先按 active/terminal 分组再按时间排序——更新的失败任务不得越过更旧的运行中任务，R7）→ 报告历史条目（`analyzed_at` 降序、`id` 决胜）。两段集合**服务端严格不相交**（R6；R9 四轮修订：不相交判定与两段读取位于同一 SQLite 快照——`/api/jobs/unified` 的历史段查询携带 `exclude_unfinished_jobs=true`，排除所有 job 行非 `completed` 的报告）：同一任务的报告已入索引但任务行尚未推进到 `completed`（索引提交与 worker 下一笔事务之间失败）时由任务行唯一代表（任务行胜出），跨段/跨页不会重复，并发提交亦然。任务条目的 `title`/`platform` 自元数据阶段成功起由任务行携带（迁移 v9 投影列；元数据未到达时为 `null`，`url` 是回退辨识）；`platform` 与 `query` 过滤同时作用于两个数据源（`platform` 两端点均不区分大小写，R8），`query` 无 title 时回退匹配 `url`。
- `query` 为历史标题的大小写不敏感子串匹配。`status` 过滤下推到 `/api/jobs` 的服务端过滤（评审 R4 修订 2026-09-11；R4b 二轮修订 2026-09-12）：`completed` 视为报告历史源单源查询（`/api/history` 的 `limit`/`offset`/`platform`/`query` 全部下推）；其余任意状态值（含 `failed`/`cancelled` 与各运行态）为任务源单源查询，**`limit`/`offset`/`platform`/`query` 全部下推**——引擎对同一过滤集合分页与计数，条目原样透传（不要求"运行中"、不二次切片），`total` 为服务端精确计数。默认视图（R4b 修订；R6 三轮修订 2026-09-14；**R9 四轮修订 2026-09-14**）：**单请求统一查询**——Bridge 调用 `GET /api/jobs/unified`（`limit`/`offset`/`platform`/`query` 下推），引擎在**同一个 SQLite 读事务（单一快照）**内完成两段计数、非完成任务排除、排序与切片：两个请求的设计里 worker 可在两次读取之间提交任务完成（任务行已返回、报告新近入段），同一任务重复且 total 双计；单一快照下整个回答（counts、排除、排序、切片）内在一致。无固定取数上限、任何 offset 不返回空窗；**`total` = 任务段总数 + 历史段总数，每页均为精确值**（两段在同一快照内严格不相交，任务行胜出）。`platform`（不区分大小写精确）与 `query`（title/url 子串，url 为元数据未到时的回退）统一服务端过滤并计入两段 total。
- 畸形载荷（R13 五轮修订 2026-09-14；R14/R15 六轮修订扩展到全部三个数据源与跨字段不变量）：三个列表数据源（jobs 单源 / history 单源 / unified）共用同一严格验证边界，每行先经 **wire 层严格模型**（Pydantic strict 模式，镜像引擎 REST schema：类型精确匹配、不转换错误类型、必填字段齐全——jobs 行 progress/created_at 必填、history 行完整 HistoryItem 字段、unified 行 progress 必填；未知新增字段忽略以容忍未来引擎）再投影到 MCP schema（投影越界如同降级）：200 响应缺失必填字段、字段类型错误（数字 job_id、布尔 progress、列表 title、`limit/offset` 回显为布尔——Python `True == 1`）、item 非 object、状态与请求过滤或段归属不符、窗口回显不一致、页容量与 min(limit, total-offset) 不符、total ≠ jobs_total + history_total、或**行位置与全局分段不符**（global_index=offset+index < jobs_total 必为 job、否则必为 history——totals 正确不能证明切片有序），一律降级 `ok:false / BRIDGE_INTERNAL`（isError=false）——绝不伪装成功空页，不纠正或忽略引擎的错误数据，也不升级为协议级错误。
- 幂等性：只读快照。
- 只读：是。
- 超时：10 秒；固定最大页长 100。
- 兼容：不在列表中嵌入报告/字幕；未知客户端应使用分页。

## 5. search_analysis_history

输入 Schema：

```json
{
  "type":"object",
  "additionalProperties":false,
  "required":["query"],
  "properties":{
    "query":{"type":"string","minLength":1,"maxLength":500},
    "limit":{"type":"integer","minimum":1,"maximum":50,"default":10},
    "offset":{"type":"integer","minimum":0,"default":0}
  }
}
```

成功：

```json
{"ok":true,"items":[{"job_id":"01J...","title":"...","matched_fields":["summary"],"snippet":"..."}],"limit":10,"offset":0,"total":1}
```

- 搜索字段：标题、作者、URL、标签、摘要、字幕。
- 错误：`QUERY_INVALID`、`SEARCH_INDEX_UNAVAILABLE`、`ENGINE_TIMEOUT`。
- 幂等性：只读快照。
- 只读：是。
- 超时：10 秒；查询与 snippet 长度受限。
- 兼容：snippet 必须是纯文本/安全 Markdown 片段，不返回整段字幕。

## 6. cancel_analysis

输入 Schema：

```json
{"type":"object","additionalProperties":false,"required":["job_id"],"properties":{"job_id":{"type":"string","minLength":1,"maxLength":64}}}
```

成功：

```json
{"ok":true,"job_id":"01J...","status":"cancelled","accepted":true,"message":"任务已取消"}
```

运行阶段可能返回 `accepted: true`、当前 status 不变及“将在安全点取消”。

- 错误：`JOB_NOT_FOUND`；终止状态不报冲突，返回 `accepted: false` 的幂等结果。
- `accepted` 定义（P4 冻结）：Engine 返回 200 且 Job 处于非终态 → `true`（`queued`/`retry_wait`/`waiting_for_model` 直接置 `cancelled`；运行中置取消标志，`message` 说明「将在安全点取消」，`status` 返回当前状态）；Job 已终态 → `false`，`status` 返回当前终态。
- 幂等性：重复取消不产生额外副作用。
- 只读：否。
- 超时：10 秒；超时后客户端应查询状态，不应假设取消失败。
- 兼容：高风险仅限当前 Job，不提供无过滤批量取消。

## 7. diagnose_environment

输入 Schema：

```json
{
  "type":"object",
  "additionalProperties":false,
  "properties":{"include_network":{"type":"boolean","default":false}}
}
```

成功：

```json
{
  "ok":true,
  "overall":"warning",
  "checks":[
    {"name":"local_engine","status":"pass","message":"Local Engine 在线","detail":null},
    {"name":"llm_api","status":"skipped","message":"未请求网络诊断","detail":null}
  ],
  "redacted":true
}
```

检查名冻结枚举（P4；顺序即输出顺序；P8 追加只增不改）：`local_engine`、`engine_version`、`database`、`report_directory`、`disk_space`、`ffmpeg`、`yt_dlp`、`llm_config`、`llm_api`、`asr_runtime`、`gpu`、`asr_models`、`cookie_browser`、`worker_runtime`。

- `asr_runtime` 覆盖本地推理运行时（sherpa-onnx + onnxruntime）与 Provider 注册状态；`asr_models` 覆盖已安装模型与激活状态；`llm_api` 仅在 `include_network=true` 时执行，否则 `skipped`。探测按 settings 凭据引用携带 Bearer 认证（与实际生成调用同规则，F1 2026-09-10）：2xx 报可达且认证有效、401/403 报认证失败（detail=`auth_failed`）、凭据库不可读报 `keyring_error` 且不探测；Key 值不出现在任何输出。

- 错误：单项失败进入 checks，不应让整个工具失败；只有 Engine 无法提供诊断时返回通用错误。
- 幂等性：逻辑只读，但可能访问网络/读取系统能力；不得下载、安装或修改配置。
- 只读：是。
- 超时：本地检查 15 秒；启用网络时总预算 30 秒，各检查独立超时。
- 兼容/隐私：默认不访问网络；路径只显示批准目录的脱敏尾部，Key/Cookie 只显示“已配置/未配置”，不回显任何字符。

