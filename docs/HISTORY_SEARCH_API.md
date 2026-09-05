# 历史与搜索 REST API — v1.0(冻结)

> 状态:**已冻结(P3 合同 2,2026-08-28)**。本文档定义 Local Engine 面向 WebUI 的
> 历史、搜索与索引管理端点;P4 的 MCP 工具(`list_analysis_jobs` / `search_analysis_history`
> / `get_analysis_report`)是这些端点的薄适配,不另立语义。
> 数据模型见 `docs/FTS5_SCHEMA.md`;文件合同见 `docs/MARKDOWN_SCHEMA.md`。

## 0. 全局约定

- 鉴权与现有 API 一致:设置 `local_access_token` 后,除 `GET /api/health` 外全部端点要求
  `X-Local-Token` 头;Engine 只绑定 `127.0.0.1`。
- 分页信封与现有 `/api/jobs` 一致:`{items, total, limit, offset}`;`limit` 有最大值,
  超界返回 422。
- 时间字段:epoch 秒(float),`published_at` 可为 `null`(平台未提供)。
- **新端点错误信封**(P3 起生效;现有端点保持原样,P4 统一,见 `.agents/progress.md` 遗留项):

```json
{"error": {"code": "STABLE_ERROR_CODE", "message": "给用户看的简洁信息"}}
```

| HTTP | code | 场景 |
|---|---|---|
| 404 | `HISTORY_ITEM_NOT_FOUND` | job_id 不在报告索引中 |
| 410 | `REPORT_FILE_MISSING` | 索引记录在,报告文件已被移出报告目录 |
| 409 | `REPORT_NOT_READY` | Job 未到 `completed`,尚无报告 |
| 422 | `QUERY_INVALID` | 搜索词为空、超长或仅含被忽略的字符 |
| 422 | `INVALID_FILTER` | 分页/过滤参数越界 |
| 503 | `SEARCH_INDEX_UNAVAILABLE` | FTS 表缺失、损坏或重建进行中且未就绪;触发重建或读取报告时报告目录未配置或不可用(不存在/非目录/无法枚举/恢复指针不可读,见 `docs/INDEX_REBUILD.md` §4) |
| 409 | `REBUILD_ALREADY_RUNNING` | 重复触发重建 |
| 500 | `REPORT_READ_FAILED` | 文件存在但读取/解码失败(哈希不匹配不在此列);报告目录解析与重建引擎共用同一来源(INDEX_REBUILD §4) |

## 1. `GET /api/history` — 历史列表

已入索引(`report_documents.doc_status = 'active'` 或 `'stale'`)的报告元数据,按
`analyzed_at` 降序固定排序。

```
GET /api/history?limit=20&offset=0&platform=youtube&language=zh&asr_provider=
```

| 参数 | 约束 | 说明 |
|---|---|---|
| `limit` | 1–100,默认 20 | |
| `offset` | ≥ 0,默认 0 | |
| `platform` | 可选 | 精确匹配小写平台标识 |
| `language` | 可选 | 精确匹配 |
| `asr_provider` | 可选 | 精确匹配;空串过滤平台字幕报告 |

响应 `HistoryListResponse`:

```json
{
  "items": [HistoryItem],
  "total": 42, "limit": 20, "offset": 0
}
```

`HistoryItem`(不嵌入摘要正文与字幕):

```json
{
  "job_id": "01J...",
  "analysis_id": "01J...",
  "title": "...",
  "platform": "youtube",
  "author": "...",
  "video_id": "...",
  "source_url": "https://...",
  "published_at": null,
  "analyzed_at": 1756350000.0,
  "language": "zh",
  "summary_mode": "auto",
  "asr_provider": "",
  "asr_model": "",
  "asr_model_version": "",
  "tags": ["选品"],
  "summary_preview": "核心摘要前 200 字符…",
  "file_path": "报告目录内相对路径.md",
  "content_hash": "sha256hex",
  "doc_status": "active",
  "indexed_at": 1756350010.0
}
```

- `doc_status` ∈ `active` / `stale` / `missing`(语义见 `docs/INDEX_REBUILD.md`)。
- `analysis_id ≡ job_id`(Markdown Schema v1 冻结);两字段都输出,是给 Schema v2 留的
  解耦缝,消费方不得假设二者永远相等之外的任何关系。
- 列表为只读快照,无副作用。

## 2. `GET /api/history/{job_id}` — 单条详情

与 `HistoryItem` 字段一致,另加完整 `core_summary`(不分页)。`missing` 状态照样返回
元数据——用户需要知道"数据库记得它",并由 `doc_status` 驱动 UI 提示。未索引 → 404
`HISTORY_ITEM_NOT_FOUND`。

## 3. `GET /api/history/{job_id}/report?section=` — 报告内容

`section` 枚举与 MCP `get_analysis_report` 完全一致(P4 直接透传):

| section | 返回内容 |
|---|---|
| `summary`(默认) | `## 核心摘要` 段落 |
| `outline` | `## 时间轴大纲` 段落 |
| `marketing` | `## 内容分析` 段落 |
| `comments` | `## 评论风向` 段落 |
| `metadata` | frontmatter + `## 视频信息` |
| `transcript` | `## 完整字幕` 段落(无字幕 → `SECTION_NOT_AVAILABLE`,404) |
| `full` | 整个文件原文 |

响应 `ReportContentResponse`:

```json
{
  "job_id": "01J...",
  "section": "summary",
  "markdown": "## 核心摘要\n...",
  "file_path": "D:\\Reports\\video-1a2b3c4d.md",
  "schema_version": 1,
  "truncated": false
}
```

- 冻结字符上限:**50,000 字符**;超限截断并置 `truncated: true`,绝不整段传输长字幕。
  MCP Bridge 可用更小的预算,REST 本身不再协商该值。
- 读取的是**文件当前内容**:用户编辑后的报告,此端点返回编辑后内容(不做哈希校验拦截;
  哈希漂移通过索引重建对账,见 `docs/INDEX_REBUILD.md` §用户修改)。
- 文件缺失 → 410 `REPORT_FILE_MISSING`;section 无内容 → 404 `SECTION_NOT_AVAILABLE`。
- 补充错误码:`SECTION_NOT_AVAILABLE`(404,仅本端点)。

## 4. `GET /api/search` — 全文搜索

```
GET /api/search?q=选品策略&limit=10&offset=0
```

| 参数 | 约束 | 说明 |
|---|---|---|
| `q` | 必填,1–500 字符 | **纯文本**,不是 FTS5 语法;引擎负责转义与分词(见 `docs/FTS5_SCHEMA.md`) |
| `limit` | 1–50,默认 10 | 固定上限 50 |
| `offset` | ≥ 0,默认 0 | |

响应 `SearchResponse`:

```json
{
  "items": [
    {
      "job_id": "01J...",
      "title": "...",
      "platform": "youtube",
      "analyzed_at": 1756350000.0,
      "snippet": "…跨境电商[选品]策略…",
      "matched_fields": ["title", "summary"],
      "doc_status": "active"
    }
  ],
  "total": 3, "limit": 10, "offset": 0
}
```

- 搜索字段(冻结):`title`、`tags`、`author`、`summary`、`transcript`、`url`。
- 排序:FTS5 `bm25` 相关度,相同分数按 `analyzed_at` 降序。
- `snippet`:FTS5 `snippet()` 生成,`[ ]` 高亮、`…` 截断,纯文本安全的片段,**绝不返回
  整段字幕**;`active` 与 `stale` 文档参与搜索(`missing` 不参与,FTS 行已删)。
- 搜索结果项包含 `doc_status`,UI 可对 `stale` 结果打「内容已编辑」徽标。
- `matched_fields`:**包含至少一个查询短语的列**,按列做 `col MATCH phrase1 OR phrase2 …`
  受限判定(最多 6 列,同事务内完成);只要文档命中就非空——AND 语义可能把不同词分散在
  不同列,按「整列满足整个查询」判定会产生误导性的空数组,故不采用。
- FTS 表缺失/损坏/重建中 → 503 `SEARCH_INDEX_UNAVAILABLE`;MCP 侧同名错误码。
- 空查询、超长、或预处理后无有效 token → 422 `QUERY_INVALID`。

## 5. 索引管理

### `GET /api/index/status`

```json
{
  "state": "idle",
  "last_finished_at": 1756350100.0,
  "last_result": {"scanned": 42, "indexed": 5, "unchanged": 35, "quarantined": 1,
                   "duplicates": 0, "removed": 1, "purged": 0},
  "open_issues": 1,
  "last_error_code": null
}
```

- `state` ∈ `idle` / `running` / `needs_rebuild`(首次回填待执行或上次重建异常中断)。

### `GET /api/index/issues` — 诊断明细

`/api/index/status` 只给数量;损坏文件、重复 `analysis_id` 的双方路径等明细在这里分页查询:

```
GET /api/index/issues?open=true&issue_code=MD_MISSING_FIELD&limit=50&offset=0
```

| 参数 | 约束 | 说明 |
|---|---|---|
| `open` | 可选,默认 `true` | `true` 只看未解决(`resolved_at IS NULL`);`false` 返回全部含已解决 |
| `issue_code` | 可选 | 精确过滤诊断码 |
| `limit` / `offset` | 1–100 / ≥ 0,默认 50 / 0 | 与全局分页约定一致 |

响应 `IndexIssuesResponse`:

```json
{
  "items": [
    {
      "issue_code": "DUPLICATE_ANALYSIS_ID",
      "relative_path": "b/报告-b.md",
      "detail": "analysis_id 01J... 已由 a/报告-a.md 索引",
      "first_seen_at": 1756350100.0,
      "last_seen_at": 1756350100.0,
      "resolved_at": null
    }
  ],
  "total": 1, "limit": 50, "offset": 0
}
```

- 排序:`last_seen_at` 降序固定;`detail` 是脱敏诊断文本,不含用户目录前缀。

### `POST /api/index/rebuild`

```json
{"purge_missing": false}
```

- 始终返回 **202** `{"state": "running"}`;已在运行 → 409 `REBUILD_ALREADY_RUNNING`。
- 报告目录未配置或不可用(不存在/非目录/无法枚举/恢复指针不可读
  `REPORT_POINTER_UNAVAILABLE`)→ 调度前**预检**直接 503
  `SEARCH_INDEX_UNAVAILABLE`,并把状态落为 `needs_rebuild`(保留具体码);
  绝不先 202 再异步失败(INDEX_REBUILD §4 多重验证)。
- `purge_missing: true` 时,磁盘上已消失的报告记录被物理删除;默认只标记 `missing`
  并移出搜索(审计优先)。
- 重建语义(算法、隔离、崩溃恢复)冻结在 `docs/INDEX_REBUILD.md`,API 不定义细节。

## 6. 明确的非目标(v1)

- 不提供报告删除/移动端点:文件归用户所有,Engine 不做破坏性文件操作。
- 不做多报告目录:单一 `report_directory`(AppSettings)。
- 无文件系统监听:索引新鲜度由重建/增量扫描保证,不做实时 watch。
- 不提供跨 Job 的全文导出或批量接口。

## 7. P4 映射(MCP 工具 → REST)

| MCP 工具 | REST | 备注 |
|---|---|---|
| `list_analysis_jobs` | `/api/history` + `/api/jobs` | 运行中任务走 `/api/jobs`,已完成历史走 `/api/history` |
| `search_analysis_history` | `/api/search` | Bridge 把 `ok` 信封套在 REST 响应外 |
| `get_analysis_report` | `/api/history/{job_id}/report` | section 枚举一一对应 |
| `get_analysis_status` / `submit_video_analysis` / `cancel_analysis` | `/api/jobs*` | P1–P2 已有,不在本合同 |
