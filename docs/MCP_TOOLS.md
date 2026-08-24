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

检查：Local Engine、数据库、报告目录、FFmpeg、yt-dlp、LLM 配置/API 连通性、CTranslate2、GPU、Whisper 模型、Cookie 浏览器、磁盘空间。

- 错误：单项失败进入 checks，不应让整个工具失败；只有 Engine 无法提供诊断时返回通用错误。
- 幂等性：逻辑只读，但可能访问网络/读取系统能力；不得下载、安装或修改配置。
- 只读：是。
- 超时：本地检查 15 秒；启用网络时总预算 30 秒，各检查独立超时。
- 兼容/隐私：默认不访问网络；路径只显示批准目录的脱敏尾部，Key/Cookie 只显示“已配置/未配置”，不回显任何字符。

