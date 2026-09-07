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
- 数据源合并（P4 冻结）：运行中（非终态）任务来自 `/api/jobs`，已完成历史来自 `/api/history`；运行中条目排在前（按提交时间降序），已按 `job_id` 去重（同一任务两侧都出现时运行中条目胜出）。运行中条目的 `title`/`platform` 为 `null`（任务表无此维度）；`platform` 与 `query` 过滤只作用于历史源。
- `query` 为历史标题的大小写不敏感子串匹配。`status` 过滤下推到 `/api/jobs` 的服务端过滤；历史源视为 `completed`，仅当 `status` 为 `null` 或 `completed` 时纳入。`total` = 运行中条数 + 历史总条数（过滤后口径）。
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

检查名冻结枚举（P4；顺序即输出顺序）：`local_engine`、`engine_version`、`database`、`report_directory`、`disk_space`、`ffmpeg`、`yt_dlp`、`llm_config`、`llm_api`、`asr_runtime`、`gpu`、`asr_models`、`cookie_browser`。

- `asr_runtime` 覆盖本地推理运行时（sherpa-onnx + onnxruntime）与 Provider 注册状态；`asr_models` 覆盖已安装模型与激活状态；`llm_api` 仅在 `include_network=true` 时执行，否则 `skipped`。

- 错误：单项失败进入 checks，不应让整个工具失败；只有 Engine 无法提供诊断时返回通用错误。
- 幂等性：逻辑只读，但可能访问网络/读取系统能力；不得下载、安装或修改配置。
- 只读：是。
- 超时：本地检查 15 秒；启用网络时总预算 30 秒，各检查独立超时。
- 兼容/隐私：默认不访问网络；路径只显示批准目录的脱敏尾部，Key/Cookie 只显示“已配置/未配置”，不回显任何字符。

