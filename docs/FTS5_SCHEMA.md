# FTS5 索引 Schema 与迁移 — v1.0(冻结)

> 状态:**已冻结(P3 合同 3,2026-08-28)**。本文档冻结迁移 v8 的精确 DDL、索引文本的
> 生成规则与查询预处理规则。实现(`storage/migrations.py` 的 `_apply_v8`)必须逐字符转录
> 本文 §2 的 DDL;`SCHEMA_VERSION` 由 7 → 8,**迁移文件归 P3 所有**(ASR-4 不触碰
> `storage/migrations.py`)。
> 设计理由见 `docs/adr/0003-p3-history-search-fts5-contracts.md`。

## 1. 设计决策(冻结)

1. **三张表,职责分离**:
   - `report_documents` — 普通表,报告元数据与文件指针,是历史 API 的数据源;
   - `report_fts` — FTS5 虚拟表,**自带文本存储**(非 external-content、非 contentless),
     `rowid == report_documents.id`;
   - `index_issues` / `index_status` — 重建诊断与状态(见 `docs/INDEX_REBUILD.md`)。
2. **FTS 表自带文本**:external-content 表的删除/更新必须提供旧值,崩溃窗口复杂;
   contentless 表不支持 `snippet()`。所有 FTS 文本都可从 Markdown 重建,自存储的冗余
   是可接受代价,换来「按 rowid 直接 delete/insert」的最简单同步协议。
3. **大文本只存一份**:`transcript` 只进 FTS 影子表,`report_documents` 不重复存;
   `summary_preview`(≤200 字符)是唯一有意的小文本冗余,服务列表页,不参与索引。
4. **分词器 = `unicode61 remove_diacritics 2` + 应用层 CJK 单字切分**:
   - SQLite 内建无 ICU;`trigram` 要求 ≥3 字符,杀死中文最常见的 1–2 字查询;
   - `unicode61` 把连续 CJK 字符视为**单个 token**,中文多字词永远无法命中——
     因此索引与查询两侧都必须做同一种确定性预处理(§4);
   - 本机 SQLite 3.50.4 已实测:切分后短语查询「选品」命中含「跨境电商选品策略」的文档。
5. **同步协议 = 单事务 upsert/delete**:报告落盘成功后,`report_documents` 与 `report_fts`
   在**同一个事务**内写入;不存在「元数据在、索引不在」的稳态(崩溃则两者都不在,
   由重建对账)。配对写必须内嵌 SAVEPOINT(`begin_nested`):Worker 捕获 Handler 异常后
   会在同一 Session 上提交失败状态,savepoint 保证半写入不随外层提交落库——原子性由
   Repository 自身保证,不依赖调用方正确回滚。

## 2. 迁移 v8 DDL(冻结,逐字符转录)

```sql
CREATE TABLE report_documents (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    job_id VARCHAR(64) NOT NULL,
    analysis_id VARCHAR(64) NOT NULL,
    title TEXT NOT NULL,
    platform VARCHAR(64) NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    video_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    published_at REAL,
    analyzed_at REAL NOT NULL,
    language VARCHAR(64) NOT NULL DEFAULT '',
    summary_mode VARCHAR(32) NOT NULL DEFAULT '',
    asr_provider VARCHAR(64) NOT NULL DEFAULT '',
    asr_model VARCHAR(64) NOT NULL DEFAULT '',
    asr_model_version VARCHAR(64) NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    summary_preview TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    byte_size INTEGER,
    doc_source VARCHAR(16) NOT NULL DEFAULT 'pipeline',
    doc_status VARCHAR(16) NOT NULL DEFAULT 'active',
    indexed_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
```

```sql
CREATE UNIQUE INDEX ix_report_documents_job_id ON report_documents (job_id)
```

```sql
CREATE UNIQUE INDEX ix_report_documents_analysis_id ON report_documents (analysis_id)
```

```sql
CREATE INDEX ix_report_documents_analyzed_at ON report_documents (analyzed_at)
```

```sql
CREATE VIRTUAL TABLE report_fts USING fts5(
    title,
    tags,
    author,
    summary,
    transcript,
    url,
    tokenize='unicode61 remove_diacritics 2'
)
```

```sql
CREATE TABLE index_issues (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    issue_code VARCHAR(64) NOT NULL,
    relative_path TEXT NOT NULL,
    detail TEXT,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    resolved_at REAL
)
```

```sql
CREATE UNIQUE INDEX uq_index_issues_open ON index_issues (issue_code, relative_path)
WHERE resolved_at IS NULL
```

```sql
CREATE INDEX ix_index_issues_open ON index_issues (resolved_at)
```

```sql
CREATE TABLE index_status (
    id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    state VARCHAR(16) NOT NULL DEFAULT 'idle',
    started_at REAL,
    finished_at REAL,
    scanned INTEGER NOT NULL DEFAULT 0,
    indexed INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    removed INTEGER NOT NULL DEFAULT 0,
    purged INTEGER NOT NULL DEFAULT 0,
    last_error_code VARCHAR(64)
)
```

```sql
INSERT INTO index_status (id, state) VALUES (1, 'idle')
```

补充约束(实现以 CHECK/应用层保证,不进 DDL 的部分必须由 repository 层强制):

- `report_fts` 的 `rowid` 必须等于对应 `report_documents.id`,由 repository 统一写入,
  任何代码不得直接向 `report_fts` 写入未经 `report_documents` 事务配对的行。
- `doc_status` ∈ `active` / `stale` / `missing`;`doc_source` ∈ `pipeline` / `rebuild`。
- `tags_json` 是 UTF-8 JSON 数组字面量,空为 `[]`。
- **open issue 去重靠部分唯一索引**:`UNIQUE (issue_code, relative_path, resolved_at)`
  对 `resolved_at = NULL` 无效(SQLite 中 `NULL != NULL`,连续插入同一 open issue 会得到
  多行)。必须用 `uq_index_issues_open`(部分唯一索引)约束「未解决」唯一;写入协议 =
  先按 `(issue_code, relative_path)` 找 open 行,命中则更新 `last_seen_at`,未命中才
  INSERT;issue 消失时置 `resolved_at`,复发作为新行留痕。
- `index_status` 不含任何进程存活信号:单 Engine 架构下「Engine 在启动」本身就证明
  任何持久化的 `running` 来自已死进程(见 `docs/INDEX_REBUILD.md` §4),不需要心跳。

## 3. 索引文本构成(冻结)

`report_fts` 六列的取值来源,全部来自 Markdown 文件解析(`docs/MARKDOWN_SCHEMA.md`):

| FTS 列 | 来源 | 归一化 |
|---|---|---|
| `title` | frontmatter `title` | §4 预处理 |
| `tags` | frontmatter `tags`,以空格连接 | §4 预处理 |
| `author` | frontmatter `author` | §4 预处理 |
| `summary` | 正文 `## 核心摘要` 段落全文 | §4 预处理 |
| `transcript` | 正文 `## 完整字幕` 段落全文;缺省为空串 | §4 预处理 |
| `url` | frontmatter `source_url` | 原样小写,不做 CJK 切分 |

不进索引:`video_id`、`summary_preview`、各时间戳、哈希、路径。

## 4. CJK 预处理合同(冻结,索引与查询两侧同构)

```
normalize_for_fts(text):
    在每个 CJK 字符前后插入空格(Han U+4E00–U+9FFF、
    平假名/片假名 U+3040–U+30FF、谚文 U+AC00–U+D7AF),
    合并连续空白,strip 首尾。
```

查询侧(`build_match_query(q)` 冻结算法):

1. 按**空白**把 `q` 切成词(word)——空白是用户唯一的显式分组手段;
2. 对每个词做 `normalize_for_fts`(CJK 单字切分);丢弃不含任何字母/数字/CJK 字符的词;
3. 每个词生成**一个短语**:词内 token 以空格连接、整体双引号包裹,词内 `"` 一律转义为
   `""`(SQL 风格);
4. 各短语之间 **AND** 连接;词数为 0 → `QUERY_INVALID`。

| 输入 | 生成查询 | 语义 |
|---|---|---|
| `选品` | `"选 品"` | 短语 = **邻接**;「选择产品」不命中 |
| `跨境电商 选品` | `"跨 境 电 商" AND "选 品"` | 空白分组 + AND |
| `foo"bar` | `"foo""bar"` | 转义后仍是合法 FTS5 语法 |
| `!!!` | — | `QUERY_INVALID` |

冻结理由:**邻接就是中文的分词信息,不可丢弃**。若把连续 CJK 切成单字符再做 AND,
「选品」会误命中「选择产品」——字符共现 ≠ 词。索引侧的单字切分天然保留邻接序列,
查询侧把连续 CJK 词组保持为一个短语,两侧即可对齐;英文按空白自然分词,单词短语
退化为普通 term,多词 AND 是标准检索行为,无需特判。

- 索引与查询使用同一 `normalize_for_fts`;任何用户输入经此管线后都是合法 FTS5 语法
  (引号已转义、无裸语法字符),引擎不应把 FTS 语法错误暴露给用户;若仍发生,
  按 `SEARCH_INDEX_UNAVAILABLE` 处理并记日志。

## 5. 同步规则(冻结)

| 事件 | 动作 |
|---|---|
| Pipeline 报告落盘成功 | 单事务:`INSERT OR REPLACE`(`job_id` 冲突时复用 `id`)**+** 删除旧行后插入 `report_fts`;`doc_source='pipeline'`,`doc_status='active'` |
| 报告文件读取时哈希 ≠ `content_hash` | 不改文件;`doc_status='stale'`;FTS 行保留。**冻结决策:stale 可搜索**——stale 是「同一文档、内容有编辑」的提示,屏蔽它只会让索引静默变不完整;FTS 行对应旧文本,点击后读到的文件是新文本,偏差由下次重建消除(`docs/INDEX_REBUILD.md` §5) |
| 索引重建发现文件消失 | `doc_status='missing'` + 删除对应 `report_fts` 行;`purge` 时物理删除 `report_documents` 行(见 `docs/INDEX_REBUILD.md`) |
| Job 被取消/失败 | 不产生任何索引行(索引只属于已完成的报告) |
| 重建解析同一 `analysis_id` 的重复文件 | 首个(路径排序)入索引,后续记 `DUPLICATE_ANALYSIS_ID` |

`INSERT OR REPLACE` 的配对协议(冻结):先 `SELECT id FROM report_documents WHERE job_id=?`
拿旧 `id`;有则 `DELETE FROM report_fts WHERE rowid=?` 再写新行,`report_documents` 复用该
`id` 更新;无则插入新行、FTS 用新 `id`。**禁止**依赖 `INSERT OR REPLACE` 的删除-重插副作用
维护自增 `id`,避免 rowid 漂移导致 FTS 悬挂行。

## 6. 完整性检查(冻结)

`POST /api/index/rebuild` 之外提供程序化校验 `verify_fts()`(诊断用,不进 REST):

- 计数口径冻结:`SELECT count(*) FROM report_fts` == `SELECT count(*) FROM
  report_documents WHERE doc_status IN ('active','stale')`(missing 已删 FTS 行,
  不参与;stale 保留 FTS 行,必须计入,否则一个 stale 文档就会造成永久假漂移);
- 每个 `active`/`stale` 文档的 `report_fts.rowid` 都存在,且无多余 FTS 行;
- 不一致 → 返回差异清单,状态置 `needs_rebuild`;**自动修复 = 触发重建**,不做原地补丁。
