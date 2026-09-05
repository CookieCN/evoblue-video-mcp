# ADR 0003: P3 历史、Markdown、FTS5 与索引重建合同冻结

- Status: Accepted
- Date: 2026-08-28

## Context

P3 的验收标准是「删除 SQLite 后可从 Markdown 重建可搜索索引」。四个合同(Markdown
Schema、历史/搜索 REST API、FTS5 Schema/迁移、索引重建行为)必须先冻结再实现,否则
索引行为、API 形状与文件格式会在实现中互相牵连漂移。同时 ASR-4 并行开发中,文件所有权
已划清:P3 拥有 `storage/migrations.py`、`reports/`、`web/` 与本组合同文档;ASR-4 拥有
`asr/`、许可证、打包与 CI。

约束:SQLite 内建分词无 ICU;实测 `unicode61` 把连续 CJK 字符视为单个 token(中文多字词
无法命中),`trigram` 要求 ≥3 字符会杀死中文最常见的 1–2 字查询;本机 SQLite 3.50.4 验证
FTS5 可用。现有代码事实:`analysis_id ≡ job_id`,报告产物已带 `relative_path` +
`content_hash` 落 `job_artifacts`,Job 已预留 `INDEXING` 阶段。

## Decision

- **Markdown Schema 冻结为 v1.0**:frontmatter 15 字段逐项定死;段落标题是解析锚点;
  `analysis_id` 在 v1 内恒等于 `job_id`(API 同时输出两字段,为 v2 留解耦缝);
  datetime 强制带时区(naive 时间无法确定 epoch,删库重建的排序不可复现)。
- **FTS5 采用「自带文本的普通 FTS5 表」**,`rowid == report_documents.id`,按 rowid 直接
  delete/insert 同步;大文本(`transcript`)只存 FTS 影子表一份。
- **中文检索 = `unicode61` + 应用层 CJK 单字切分**:索引与查询两侧同一 `normalize_for_fts`;
  查询按空白分词,**连续 CJK 词组保持为一个短语(邻接语义),词内引号双写转义**,词组间
  AND。评审修正:最初把「选品」误设计成字符 AND——邻接就是中文的分词信息,字符 AND
  会把「选品」误命中到「选择产品」;短语语义经最小反例实证。查询入口是纯文本,不暴露
  FTS5 语法。
- **只有一个重建算法**:确定性全目录重扫 + 哈希跳过;purge 只是 `missing` 记录的显式
  物理删除,不构成第二条路径。崩溃恢复 = 幂等重跑,不保存扫描游标;启动对账把持久化
  `running` **一律孤儿化**(单 Engine 绑定端口,「正在启动」即证明旧进程已死,不设心跳
  宽限)。重复 `analysis_id` 的胜者只由**当前文件集的排序最小路径**决定,与数据库历史
  无关——否则「删库重建」与「保留库重建」收敛到不同状态。
- **stale 语义统一**:可搜索、保留 FTS 行、计入 `verify_fts()`;偏差由下次重建消除。
  `index_issues` 的 open 去重靠部分唯一索引(表级 UNIQUE 含 NULL 列无效)。
- **新端点引入结构化错误信封** `{"error":{code,message}}`,旧端点保持原样到 P4 统一。
- **重建永不写用户的 `.md` 文件**;用户编辑经重建被采纳为新的已知状态,与
  ReportWriter 的冲突副本保护共同构成「不静默覆盖」闭环。

## Consequences

- 迁移 v8(`SCHEMA_VERSION` 7→8)可从合同 DDL 逐字符转录,新装与升级路径收敛一致。
- 中文 1–2 字查询可用;代价是 CJK 无词级相关度(bm25 按字符短语计分),对长字幕排序
  精度有限——v1 接受,基准是「能命中、可分页、有摘要」,不是搜索引擎。
- `report_fts` 冗余存储转写文本(与 Markdown 各一份);换来 contentless/external-content
  都没有的最简同步协议与 `snippet()` 支持。
- 重建期间新完成的报告留待下次重建收录(批间最终一致),不阻塞 Worker。
- P4 的 `list_analysis_jobs` / `search_analysis_history` / `get_analysis_report` 直接映射
  REST 端点,Bridge 保持薄适配。

## Rejected alternatives

- **external-content FTS5 表**(`content='report_documents'`):更新/删除必须提供旧值,
  崩溃窗口处理复杂,收益只是省一份文本存储。
- **contentless FTS5 表**:不支持 `snippet()`,而摘要是搜索结果的核心输出。
- **trigram 分词器**:≥3 字符下限与中文 1–2 字高频查询直接冲突。
- **ICU/tokenizers 扩展或 jieba 等外部分词**:引入原生依赖或 Python 运行时开销,且
  SQLite 内建 FTS5 + 单字切分已满足合同;词级分词留作后续版本可选增强。
- **全量 drop-then-rebuild 重建**:与 Worker 并发时存在丢失新写入的窗口,且崩溃中途
  留下空索引;重扫 + 哈希跳过天然幂等且无此爆炸半径。
- **文件系统监听保持索引新鲜**:Windows watch 事件噪音大、与便携盘/网络盘兼容性差;
  P3 用按需重建,实时 watch 列入非目标。
