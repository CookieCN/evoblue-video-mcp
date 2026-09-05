# 索引重建行为 — v1.0(冻结)

> 状态:**已冻结(P3 合同 4,2026-08-28)**。本文档冻结「扫描 Markdown → 重建索引」的
> 唯一算法、触发条件、崩溃恢复与验收标准。表结构见 `docs/FTS5_SCHEMA.md`,
> 解析规则见 `docs/MARKDOWN_SCHEMA.md`,HTTP 表面见 `docs/HISTORY_SEARCH_API.md`。

## 1. 核心原则(冻结)

1. **Markdown 是事实,数据库是投影**:任何时候删除 SQLite 文件,重启后 Engine 都能从
   报告目录恢复完整可搜索历史。这是 P3 的验收标准,本合同其余部分都是它的细则。
2. **只有一个重建算法**:所谓 full rebuild 与 incremental 是同一算法——确定性全目录重扫,
   未变化文件按哈希跳过。「full」仅体现为 `purge` 语义(§5)。不存在第二条代码路径,
   消除两套行为漂移的可能。
3. **重建永不删除、永不重写用户的 `.md` 文件**:只读扫描。唯一写目标是数据库。
4. **用户修改优于生成内容**:重建以文件**当前内容**为准重新解析入索引,并更新
   `content_hash`——用户编辑被采纳为新的已知生成状态。

## 2. 扫描规则(冻结)

| 规则 | 冻结值 |
|---|---|
| 范围 | `report_directory`(AppSettings)整棵子树,递归 |
| 文件选择 | 仅 `*.md`(大小写不敏感) |
| 跳过 | `.` 开头的目录与文件;`*.tmp`;`*.conflict-*.md` **不跳过**(冲突副本是用户可见文件,正常入扫) |
| 排序 | 相对路径逐字节排序(POSIX 分隔符),确定性处理顺序 |
| 批量 | 每批 50 个文件一个事务;批间提交 |

文件身份与分类,对每个文件按序执行:

1. `unchanged`:路径 + `content_hash` 均与 `report_documents` 现行记录一致 → 跳过
   (只允许计数器 +1)。
2. `update`:同一路径、同 `analysis_id`、哈希不同 → 重新解析,单事务更新
   `report_documents` + `report_fts`(FTS5_SCHEMA §5 配对协议),`doc_status='active'`,
   `doc_source='rebuild'`。用户编辑由此生效。
3. `new`:解析成功且该 `analysis_id` 尚无占位(目录内未见、数据库也无)→ 插入。
4. `duplicate`:`analysis_id` 已被其他路径占用 → 按归属判定分出胜败,**败者**不入索引,
   记 `DUPLICATE_ANALYSIS_ID` issue(detail 含双方路径、`analyzed_at` 与哈希)。
5. `quarantined`:解析失败 → 记 issue,文件原样保留,不进索引。

**归属判定(冻结)**:同一 `analysis_id` 的胜者 = **当前报告目录内携带该 ID 的排序最小
路径**,与数据库历史无关。按排序顺序单趟处理即可实现:先见者占位;后见者若路径排序
更小,则取代占位(以配对协议替换索引行,原占位路径改记 `DUPLICATE_ANALYSIS_ID`);若
排序更大,后见者自己记 `DUPLICATE_ANALYSIS_ID`。因此**同一文件集合在「数据库保留」与
「数据库已删」两种前提下收敛到同一胜者**——胜者只由文件集合决定,不由旧数据库历史
决定。这是 §1.1「删除 SQLite 收敛到相同状态」的必要条件。

## 3. 诊断码(冻结)

| code | 严重度 | 含义 |
|---|---|---|
| `MD_FRONTMATTER_INVALID` | blocking | 无 frontmatter / YAML 不可解析 / 定界符缺失 / 字段值非冻结序列化形态 |
| `MD_UNKNOWN_SCHEMA_VERSION` | blocking | `schema_version` 缺失、非整数或不属于已知版本 |
| `MD_MISSING_FIELD` | blocking | 必填识别字段缺失、为空或值非法(`analysis_id`、`source_url`(须 HTTP(S))、`platform`(须小写)、`video_id`、`title`、`analyzed_at`(须带时区)、`summary_mode`(须三值枚举)) |
| `MD_BODY_STRUCTURE_INVALID` | blocking | 正文必现锚点缺失或不匹配:`# {title}` 一级标题、`## 原始链接` 段落(MARKDOWN_SCHEMA §3) |
| `MD_ENCODING_ERROR` | blocking | 非 UTF-8 / 含 BOM 解析失败 |
| `MD_EMPTY_BODY` | warning | 识别字段齐全但无任何分析段落;**仍入索引** |
| `DUPLICATE_ANALYSIS_ID` | blocking | §2.4 |

- blocking → 文件不入索引(`quarantined` 计数);warning → 入索引但留痕。
- issue 记入 `index_issues`:**open 状态由部分唯一索引 `uq_index_issues_open` 去重**
  (`(issue_code, relative_path) WHERE resolved_at IS NULL`;表级 UNIQUE 含 NULL 列
  无去重效果);写入协议见 `docs/FTS5_SCHEMA.md` §2 补充约束。后续扫描中问题消失 →
  置 `resolved_at`,复发作为新行留痕,不物理删除。
- **诊断 `detail` 一律脱敏**(经 `GET /api/index/issues` 返回并持久化):只允许稳定码、
  异常**类型**与相对路径,禁止 `str(OSError)` 等原始异常消息(其中必含报告绝对路径)。
  原始消息只进本地日志。
- 隔离列表通过 `GET /api/index/status` 的 `open_issues` 暴露,明细走
  `GET /api/index/issues`(`docs/HISTORY_SEARCH_API.md` §5);**永不自动删除文件**。

## 4. 状态机与崩溃恢复(冻结)

```
idle ──触发──▶ running ──完成──▶ idle
  ▲               │
  │               └──异常退出/进程终止──▶ needs_rebuild
  └──────────── 校验失败(FTS5_SCHEMA §6)─┘
```

- **重启对账**(Engine 启动时,顺序执行):
  1. **任何持久化的 `running` 一律视为孤儿**,直接置 `needs_rebuild`。依据:Engine 是
     单实例、只绑定一个 loopback 端口——「本进程正在启动」本身证明没有其他存活 Engine,
     持久化的 `running` 必然来自已死进程。不设心跳宽限期:心跳只能证明「曾经活着」,
     证明不了「现在活着」,快速崩溃重启会让索引在宽限期内无人推进(违反 §1.1 自愈)。
  2. `needs_rebuild` → 自动触发重建(见 §5);
  3. `report_documents` 为空且报告目录含 ≥1 个 `.md` → 置 `needs_rebuild` 并自动触发
     (首次启动 / 数据库删除后的自愈路径)。
  4. **`report_documents` 为空且权威根不可用** → 直接落
     `needs_rebuild`/`ROOT_UNAVAILABLE`(保留具体码),不停留在误导性的 `idle`——
     状态必须解释「历史为什么是空的」。
  5. 其余(`idle`)不干预。自动重建因根不可用失败时,**具体码必须保留**
     (`ROOT_UNAVAILABLE` / `REPORT_POINTER_UNAVAILABLE`),不得被外层兜底覆盖为
     `REBUILD_FAILED`。
- **报告目录的权威来源**(删库自愈的前提):`report_directory` 存于 SQLite,随删库一同
  丢失。Engine 用两层数据库外持久化保证可恢复:①数据目录内的**指针文件**
  (`report-root.txt`,非敏感路径,WebUI 每次保存设置时同步写入);②启动注入的**回退
  目录**(平台用户数据目录下的 `reports/`,即默认值)。解析顺序 = settings 值 → 指针
  文件 → 回退目录 → 无。**解析命中的候选即权威**:自定义指针不可用**不得**静默回退
  到回退目录——对错误目录重扫会把原目录的全部记录判为 `missing`,比失败更糟。指针
  文件「存在但不可读」(权限变化/占用/损坏)同样 fail-closed:抛稳定码
  `REPORT_POINTER_UNAVAILABLE` 并落 `needs_rebuild`,**不得**折叠为「无指针」去扫
  回退目录。
- **指针文件写入必须崩溃安全**:同目录临时文件 + fsync + 原子 `os.replace`。原地截断
  写(`Path.write_text`)在崩溃/写盘失败时会留下空文件或半文件,恰好破坏它唯一要保证
  的灾难恢复能力。**清空同样走原子替换**:写入空墓碑(而非 `unlink`),任何失败向
  调用方传播、由设置端点以 503 拒绝整次保存——静默失败的清空会让数据库置 NULL 而
  旧指针残留,删库后复活旧目录。
- **设置双写(指针先行 + 串行化 + 补偿)**:保存 `report_directory` 时先原子写指针、
  再提交 SQLite;「读当前设置 → 写指针 → 提交数据库」由进程内锁**串行化**,并发 PUT
  不得交错成「指针=B、数据库=A」;数据库提交失败时**补偿恢复旧指针**再返回 503——
  失败的保存绝不能留下「新指针 + 旧数据库」。指针写与补偿在事件循环内**同步执行**
  (不进线程池):两者构成不可分割的临界区——取消不会落在 replace 与补偿之间,被
  取消的线程也不会迟到地用旧值覆盖补偿结果;设置保存低频,fsync 阻塞是可接受的
  代价。补偿覆盖**取消**(CancelledError 是 BaseException):取消同样恢复旧指针后
  再传播。两个存储无法真正原子:指针只在删库后生效、settings 存活时以 settings 为
  准,补偿后的唯一残留窗口是「补偿写也失败」的双重磁盘故障,记日志暴露。
- **可用性判定与多重验证**:`ROOT_UNAVAILABLE` = 未配置、不存在、不是目录、或无法
  枚举(失效指针/目录改名/外接盘离线/权限变化)。该判定在四处执行:①**端点预检**
  (`POST /api/index/rebuild` 调度前;失败先落 `needs_rebuild`/`ROOT_UNAVAILABLE`
  再 503,状态可见而非停留在过期 idle);②**扫描入口**(实际扫描前重复判定,关闭
  预检与扫描之间的 TOCTOU 窗口);③**批处理读失败时**(逐文件读失败只在外接盘仍在
  时才按单文件隔离——目录掉线后每个读取都会失败,逐文件隔离会把整棵目录交给
  absence pass 全量清除,故先探测根,失效即终止整轮);④**absence pass 之前**
  (枚举与批读取都成功过,不代表现在仍可枚举;缺失判定与物理 purge 是破坏性操作,
  必须对着「现在仍可见」的根执行)。不可用即失败:**绝不把缺目录当作空目录执行
  absence pass**——否则一次手动 purge 会把全部索引记录物理删除;扫描中途根失效必须
  终止整轮并保留原索引。
- **读取端共用解析器**:历史详情与报告内容端点(`docs/HISTORY_SEARCH_API.md`
  §2/§3)与重建引擎共用同一解析顺序——删库恢复后的索引不仅可列出、可搜索,还必须
  可完整读取;否则「恢复」只是半个功能。读取端同样 fail-closed:指针不可读返回
  503 `SEARCH_INDEX_UNAVAILABLE`,绝不回退读错误目录。
- **purge 的物理删除范围**:仅限「原路径在磁盘上确实已消失」的记录。文件仍存在但本轮
  不可认领(损坏/改 `analysis_id`/重复败者)的记录只 `missing` 退出搜索、保留审计,
  `purge_missing=true` 也不删除。
- **崩溃恢复 = 幂等重跑**。重建中间态只存在于已提交批次;重跑从未完成处继续的效果,
  由 `unchanged` 跳过自然达成,**不保存扫描游标**(50 文件/批的重扫开销可忽略,游标
  状态本身就是新的崩溃面)。孤儿化判定让「kill 后 3 秒重启」与「kill 后 1 小时重启」
  走同一条路,无时间窗口差异。
- 同一时刻最多一个重建任务(进程内互斥;409 `REBUILD_ALREADY_RUNNING` 拒绝并发触发)。

## 5. 触发条件(冻结)

| 触发 | 方式 | purge |
|---|---|---|
| 首次启动回填 / 数据库删除后自愈 | 自动(§4 第 3 步) | 否 |
| `needs_rebuild` 状态启动(含孤儿化落点) | 自动(§4 第 1–2 步) | 否 |
| 用户在 WebUI 触发 | `POST /api/index/rebuild` | 请求体指定,默认否 |
| `verify_fts()` 发现不一致 | 自动置 `needs_rebuild` | 否 |

`purge=true`(仅显式请求):磁盘已消失且状态为 `missing` 的记录物理删除,
计数进 `purged`;默认路径只标记 `missing` + 移出 FTS,保留审计痕迹。
`missing` 记录不参与搜索与历史列表默认视图(`doc_status` 字段仍可查)。

文件被用户编辑(读路径发现哈希漂移 → `stale`)**不立即触发重建**;偏差在下一次重建
(手动,或启动对账自动)时由 §2.2 `update` 分类消除。stale 的完整语义见
`docs/FTS5_SCHEMA.md` §5:可搜索、保留 FTS 行、计入 `verify_fts()`。

## 6. 与 Worker 的并发(冻结)

- 重建在 Engine 进程内后台任务执行,不阻塞 API 与 Worker。
- **写事务一律 `BEGIN IMMEDIATE`**(这是「由 SQLite 写锁串行化」的真实机制):
  WAL 模式下,deferred 事务「先读后写」在另一写者于其读快照建立后提交时,写升级会
  得到**不可重试**的 `BUSY_SNAPSHOT`(`database is locked`,`busy_timeout` 对快照
  冲突不生效)。因此所有「先读后写」的事务都以 `BEGIN IMMEDIATE` 开写:拿到写锁后
  不可能再有新提交插入,冲突退化为可等待的 busy timeout。
- **扫描批是两阶段结构**:阶段一在**无事务**状态下完成文件读取与解析(重建绝不
  跨磁盘 I/O 持有写锁;读失败时的根目录探测也在此阶段);阶段二用**短 IMMEDIATE
  事务**只做数据库对账(诊断、归属、unchanged、upsert)——毫秒级,绝不吃慢盘。
- **治理边界**:①自持事务的 repository 写函数(自己 commit 的)在入口统一
  `_begin_immediate`(委托 `db.ensure_immediate_transaction`),且**由结构守卫测试
  按 AST 强制**:守卫必须是函数体首语句(先于任何读写),新增写函数自动被门禁
  抓住;guard 的事务状态感知靠 **SQLite authorizer** 的数据库级写动作检测
  (prepare 时报告;`session.new/dirty/deleted` 看不见已 `execute()` 的 Core DML,
  SQL 字符串前缀匹配会漏注释/CTE/触发器写入)。authorizer 只在语句 **prepare**
  时触发,而 sqlite3 模块默认按连接缓存 prepared statement——同一连接第二次执行
  相同 SQL 字符串直接复用缓存、不再 prepare、authorizer 静默失明(参数化的
  Core/ORM DML 共享同一 SQL 字符串,在池化连接上这是常态而非特例);因此
  `build_engine` 必须以 `cached_statements=0` 禁用语句缓存,保证每条语句重新
  prepare、每个写动作都被观测(本应用写路径低频,重复 prepare 的开销可忽略)。
  写动作集 = **所有可能改变数据库或连接持久状态的动作**:行数据(INSERT/UPDATE/
  DELETE,含触发器内部写入)、模式(建/删/改表、索引、视图、触发器、虚表)、
  PRAGMA(`user_version` 等可写 PRAGMA 持久化;**读 PRAGMA 报同名动作码、一并
  计入**——fail-closed:拒绝一个只读事务代价微小,静默发布未持有的写不可接受)、
  REINDEX/ANALYZE/ATTACH/DETACH;明确不计入的只有纯读(READ/SELECT/RECURSIVE)、
  函数调用(FUNCTION——bm25/snippet 属搜索读路径)与事务控制(TRANSACTION/
  SAVEPOINT,不写数据且为配对写机制载体):IMMEDIATE 事务内
  幂等返回;带 DML 或未确认 ORM 变更的 deferred 事务**拒绝**(绝不静默发布调用方
  写入);只读 deferred 事务用**空 commit** 关闭(rollback 会 expire 调用方已加载
  实例,故不用)。②不自持事务的配对写(`report_documents`/`report_fts`,
  FTS5_SCHEMA §5)由调用方负责,调用方为重建批事务、Pipeline 索引写入、Worker
  状态推进——均已治理;③helper 的语义是「成功才提交、任何异常(含取消)回滚、
  finally 只恢复 deferred 选项」——绝不在异常/取消路径提交半完成写,失败提交必须
  传播;且 **commit 本身取消安全且抗重复取消**:取消可能落在 commit 的 await 上而
  SQLite 其实已经落盘,故 commit 经独立任务执行、verdict 等待**循环 shield**——
  任何后续取消都落在 shield 上、verdict 永不被取消,直到 commit task 真正完成才
  裁决:落盘成功则保留新状态再传播取消(此时补偿反而制造分裂),落盘失败才向上
  传播错误交由调用方补偿;写检测不解析 SQL 字符串(注释前缀、`WITH...UPDATE`、
  触发器内部写入都会漏),而用 **SQLite authorizer**(prepare 时数据库级报告
  写动作,只观察不拦截);④需要协调文件双写的端点(设置保存)以 `commit=False`
  调用写函数,
  让 commit 成为该事务的最后一步:「进入异常处理 ⟺ 数据库未采用新值」,补偿因此
  精确,post-commit 失败/取消绝不会错误回滚指针。
- 重建期间 Pipeline 完成新报告:Pipeline 的索引写入(FTS5_SCHEMA §5 单事务)与重建
  批事务由上述 IMMEDIATE 写锁串行化;重建对「扫描之后新出现的文件」无认知——它不
  回滚、不覆盖 Pipeline 写入的行,该文件留给**下一次**重建收录。这是可接受最终一致
  性,不是缺陷。
- 重建期间搜索:`unchanged`/已提交批次可正常命中;无部分行可见(批事务保证)。

## 7. 验收标准(冻结,来自 PRD P3)

1. 生成 ≥3 份报告 → **删除 SQLite 数据库文件** → 重启 Engine → 自动回填后:
   `GET /api/history` 返回全部报告元数据;`GET /api/search` 用中文词命中正确文档与摘要。
2. 用户编辑某报告正文 → 重建 → 搜索命中**编辑后**内容;`content_hash` 更新为文件现值;
   再次运行同 Job 生成报告时走冲突副本路径,不覆盖用户编辑。
3. 手工破坏一个文件的 frontmatter → 重建 → 该文件进入 `index_issues`,其余文件不受影响;
   文件本身未被修改或删除。
4. 两个文件携带相同 `analysis_id` → 重建 → 排序第一者入索引,冲突进入诊断列表,
   明确报告双方路径。
5. 重建中途 kill Engine → 重启 → 状态对账为 `needs_rebuild` → 自动重跑 → 最终计数一致,
   不产生重复行。
6. 全程不产生对任何 `.md` 文件的写入。
7. **短语邻接反例**:文档 A 含「选品策略」、文档 B 含「选择产品」;查询「选品」命中 A、
   **不命中 B**(字符共现不是命中,见 `FTS5_SCHEMA.md` §4)。
8. **引号转义**:查询 `foo"bar` 不产生 FTS5 语法错误,且能命中含该原文的文档。
9. **open issue 去重**:同一文件同一诊断码连续两次记录,`index_issues` 中只有一行
   open 记录(`last_seen_at` 更新);解决后复发产生新行。
10. **stale 语义**:标记 `stale` 后文档仍可搜索,`verify_fts()` 不报漂移;`missing`
    后不可搜索且 `verify_fts()` 仍不报漂移。
11. **快速崩溃重启**:重建中途 kill,3 秒内重启 → 启动对账直接置 `needs_rebuild`
    (无心跳宽限窗口),自动重跑,最终计数一致。
12. **重复 ID 收敛**:文件 `a.md` 与 `b.md` 携带同一 `analysis_id`——先只索引 `b.md`
    再放入 `a.md` 重建,与删除数据库直接重建,两种路径的胜者都是 `a.md`(排序最小),
    `b.md` 进诊断列表。
13. **删库恢复后内容可读**:删除 SQLite → 重启自动回填后,`GET /api/history/{id}`
    返回完整 `core_summary`;`GET /api/history/{id}/report` 的 `summary`/`metadata`/
    `full` 返回 200 且内容来自恢复目录(读取端与重建共用目录解析器,§4;文件存在但
    无该 section 时,`SECTION_NOT_AVAILABLE` 404 即证明读取与解析成功)。
14. **不可用根目录绝不执行 absence pass**:解析结果不存在/非目录/无法枚举时,连
    `purge_missing=true` 的重建也必须失败(端点 503,状态落
    `needs_rebuild`/`ROOT_UNAVAILABLE`),现有索引记录与搜索不受影响;失效的自定义
    指针不得静默回退到回退目录。预检通过后、扫描前目录消失(TOCTOU)同样失败安全。
15. **扫描中途根失效同样失败安全**:`_collect` 完成后目录消失(外接盘掉线),后续
    逐文件读失败不得按单文件隔离后执行 absence pass——整轮必须以
    `needs_rebuild`/`ROOT_UNAVAILABLE` 终止,现有记录与搜索不受影响;全部读成功、
    absence pass 前目录消失同样终止。
16. **指针双写一致与 fail-closed**:并发 PUT 串行化后,两个 200 的终态必然
    「指针 = 数据库」;数据库提交失败时指针补偿恢复并 503;清空走原子墓碑、失败
    阻止数据库提交;不可读指针以 `REPORT_POINTER_UNAVAILABLE` fail-closed,绝不
    回退扫描健康回退目录;启动对账在库空且根不可用时落
    `needs_rebuild`/具体码;诊断 `detail` 不含绝对路径。

以上每条都必须有对应自动化测试(P3 实现,测试文件归 P3 所有,放 `tests/` 下不与
ASR-4 的 `tests/asr/` 重叠)。
