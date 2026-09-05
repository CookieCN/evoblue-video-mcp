# Markdown Report Schema — v1.0(冻结)

> 状态:**已冻结(P3 合同 1,2026-08-28)**。本文档是报告文件的唯一权威定义;实现必须与
> `src/evoblue_video_mcp/reports/schema.py`、`renderer.py`、`writer.py` 保持一致。
> 任何不兼容变更必须提升 `schema_version` 并先修订本文档。

Markdown 报告是本产品的最终事实资产:数据库可以整体删除,知识必须能从这些文件完整恢复
(见 `docs/INDEX_REBUILD.md`)。因此本合同同时约束**生成方**(Pipeline 渲染)与**读取方**
(索引重建解析器):生成方写出的每个字段,读取方必须能无损解析回来。

## 1. 文件基本约束

| 项 | 冻结值 |
|---|---|
| 编码 | UTF-8,无 BOM |
| 换行 | LF(`\n`);**读取侧容忍 CRLF**(Windows 编辑器常见,解析前归一为 LF),生成侧强制 LF |
| 格式 | YAML Frontmatter + 标准 Markdown(不依赖 Obsidian 专属语法) |
| Frontmatter 定界 | 首行 `---`,字段区,单独一行 `---` 结束 |
| 内容哈希 | 文件全部字节的 SHA-256(即 `content_hash`,不含任何前缀) |

## 2. Frontmatter 字段表(v1)

字段名、类型、语义逐项冻结。生成方按 `renderer.py` 顺序输出;读取方按字段名解析,
**顺序无关**;未知字段必须忽略(向前兼容),不得报错。

| 字段 | 类型 | 必填 | 语义与约束 |
|---|---|---|---|
| `schema_version` | integer | 是 | 字面量 `1`。其他值 → 索引重建按 `MD_UNKNOWN_SCHEMA_VERSION` 隔离 |
| `analysis_id` | string | 是 | 分析标识。**v1 冻结:`analysis_id` 恒等于生成它的 `job_id`**。全局唯一,是历史/搜索 API 的主键之一 |
| `source_url` | string | 是 | 视频 URL(HTTP/HTTPS) |
| `platform` | string | 是 | 平台标识,小写:`youtube` / `bilibili` 等 |
| `video_id` | string | 是 | 平台侧视频 ID |
| `title` | string | 是 | 视频标题;同时决定默认文件名 |
| `author` | string | 否(默认 `""`) | 作者/UP 主 |
| `published_at` | datetime 或 `null` | 否(默认 `null`) | 视频发布时间;平台未提供时保持 `null`,不得伪造 |
| `analyzed_at` | datetime | 是 | 报告生成时间,RFC 3339(带时区偏移) |
| `summary_mode` | string | 是 | `auto` / `standard` / `unboxing` 三值之一 |
| `language` | string | 否(默认 `""`) | 字幕/转写语言;平台字幕流程保留实际语言,不可用时为空串 |
| `asr_provider` | string | 否(默认 `""`) | **平台字幕流程必须为空串**;本地 ASR 写实际 Provider 标识 |
| `asr_model` | string | 否(默认 `""`) | 同上,实际模型 ID |
| `asr_model_version` | string | 否(默认 `""`) | 同上,实际模型版本 |
| `tags` | string 数组 | 否(默认 `[]`) | 标签;可为空数组 |

序列化规则(与 `renderer.py` 冻结一致):

- 字符串值使用 JSON 双引号转义(`json.dumps(value, ensure_ascii=False)`)——这同时是合法的
  YAML 双引号标量,读取方用标准 YAML 解析器即可。
- `tags` 输出为 JSON 数组字面量。
- `published_at` 为 `null` 时输出字面量 `null`,不得省略该行。
- datetime 输出 `isoformat()` 结果(RFC 3339,带 UTC 偏移)。
- **时区强制**:所有 datetime 字段必须带时区偏移(tz-aware);naive datetime 在写入侧
  (`reports/schema.py` validator)和重建解析侧都被拒绝,按 `MD_MISSING_FIELD` 同级处理。
  理由:naive 时间无法确定 epoch,删除数据库后重建的历史排序将不可复现。

## 3. 正文段落

正文以 `# {title}` 一级标题开头,后接固定顺序的二级标题段落。**段落即解析锚点**:
索引重建通过精确匹配二级标题切分正文,回填结构化字段。

| 顺序 | 标题(精确匹配) | 对应数据 | 出现规则 |
|---|---|---|---|
| 0 | `# {title}` | frontmatter `title` | 必现 |
| 1 | `## 核心摘要` | `core_summary` | 内容非空才输出;解析时可选 |
| 2 | `## 核心收获` | `key_takeaways`(每行 `- ` 列表项) | 列表非空才输出;解析时可选 |
| 3 | `## 时间轴大纲` | `timeline_outline` | 同上 |
| 4 | `## 内容分析` | `content_analysis` | 同上 |
| 5 | `## 评论风向` | `comment_sentiment` | 同上 |
| 6 | `## 视频信息` | `video_information` | 同上 |
| 7 | `## 原始链接` | `source_url` | **必现**(唯一保证输出的段落) |
| 8 | `## 完整字幕` | `transcript` | 仅当存在转写时输出;解析时可选 |

解析规则:

- 段落边界 = 下一处精确匹配的二级标题;标题行必须整行匹配(前导 `## ` + 冻结标题)。
- 段落内容与标题之间的空行不属于内容。
- **必现锚点**:正文必须以 `# {title}` 一级标题开头,且必须含 `## 原始链接` 段落;
  缺失或不匹配按 `MD_BODY_STRUCTURE_INVALID` 隔离(`docs/INDEX_REBUILD.md` §3)。
- 字段值约束在解析侧强制:`source_url` 必须是绝对 HTTP(S) URL,`platform` 必须小写,
  `summary_mode` 必须是三值枚举,datetime 必须带时区;违规按 `MD_MISSING_FIELD` 处理。
- 段落缺失 ≠ 错误:重建解析器把缺失段落视为空值。分析段落(核心摘要至视频信息)全部
  缺失仍可入索引,但 `MD_EMPTY_BODY` 计入诊断。
- 正文用户编辑:**不回写** frontmatter;索引重建以文件当前内容为准(见
  `docs/INDEX_REBUILD.md` §用户修改)。

## 4. 命名与写入(与 `writer.py` 冻结一致)

- 默认文件名 = `{safe-title}-{content_hash 前 8 位}.md`,内容寻址保证不同内容永不互撞。
- `safe-title` 清洗规则:移除 `< > : " / \ | ? *` 与控制字符;去掉尾随点/空格;Windows
  保留名(CON/PRN/AUX/NUL/COM1-9/LPT1-9)后缀 `-analysis_id`;超长截断到 200 字符;
  清洗后为空则回退 `analysis-{analysis_id}`。
- 同目录临时文件 + `fsync` + 原子 `os.replace`,不产生半文件。
- **不覆盖用户修改**:数据库持有上次生成内容的哈希;目标文件存在且哈希不匹配上次生成
  哈希时,生成冲突副本 `{stem}.conflict-{8 位随机}.md`,绝不静默覆盖。
- 文件名模板若开放配置,产出仍必须通过同一清洗器,且末尾必须保留内容哈希段。

## 5. 与其他合同的边界

| 依赖方 | 使用方式 |
|---|---|
| FTS5 索引(`docs/FTS5_SCHEMA.md`) | 索引文本 = frontmatter 识别字段 + 正文段落,按本合同切分 |
| 索引重建(`docs/INDEX_REBUILD.md`) | 解析失败/未知版本的文件进隔离清单,不删除、不重写 |
| 历史/搜索 API(`docs/HISTORY_SEARCH_API.md`) | `GET /api/history/{job_id}/report` 返回的 `markdown` 即本合同文件原文 |

## 6. 版本演进

- 新字段一律可选;旧读取器忽略未知字段,新读取器容忍缺失可选字段。
- 破坏性变更(改字段名/类型/必填性、改段落标题)必须提升 `schema_version`,提供迁移器、
  备份与回滚;迁移不得静默覆盖用户编辑——先生成新文件或经确认后原子替换。
- `schema_version` 同时被 FTS 索引与重建流程用于路由到对应版本的解析器;未知版本一律
  隔离,不猜测、不丢弃。
