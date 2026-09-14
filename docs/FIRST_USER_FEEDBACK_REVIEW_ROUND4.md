# 第一个适用者反馈修复：第四轮代码评审

评审日期：2026-09-14

## 结论

**本轮仍不能关闭。** 第三轮的四个静态反例已经翻转，但实现仍有 2 个 P1 阻断项和
1 个 P2 凭据清理缺口。根因都是多资源/多请求之间缺少一个可判定的提交点；现有测试只覆盖
静态数据和普通异常，没有覆盖状态在两个查询之间变化、以及取消落在 durable commit 窗口。

## Findings

### [P1] 两个独立 HTTP 请求没有共同快照，所谓“不相交”会在 job 完成时失效

位置：`src/evoblue_video_mcp/mcp/server.py:288-340`、
`src/evoblue_video_mcp/storage/report_repository.py:765-789`

默认列表先请求 `/api/jobs`，再请求 `/api/history?exclude_unfinished_jobs=true`。每个请求创建
自己的数据库 session/事务，因此排除条件只能保证 history 查询执行时的状态，不能保证与前一
次 jobs 查询看到同一快照。

确定性反例：

1. jobs 请求时，报告已经入索引，job 仍为 `indexing`；jobs 返回 `dup`；
2. 两个请求之间 Worker 把 job 推进到 `completed`；
3. history 请求执行排除条件时，`dup` 已是 completed，因此又返回 `dup`；
4. 当前结果为 `items=[dup, dup]`、`total=3`。

这正是产品运行时的正常状态变化，不是损坏数据。当前有状态 fake 测试预先把两个集合处理成
静态不相交，绕过了需要验证的竞争窗口。

应采用上一轮建议的 Engine 统一列表端点，在同一 SQLite 快照中完成 jobs/history 的分组排序、
排除、count、offset 与 limit，Bridge 只发一次请求。若仍使用 offset 跨多次 MCP 调用，合同还
应避免承诺活跃 Worker 下的跨调用稳定快照；若必须承诺，需要 cursor/snapshot，而不只是单页
统一查询。

必须补测试：在 jobs 查询返回后、history 查询前把同一 job 从 indexing 推到 completed，旧
双请求实现应转红；统一端点应在一个数据库快照内返回一次且 total 精确。

### [P1] durable commit 后收到取消，数据库采纳新目录但代码恢复旧报告指针

位置：`src/evoblue_video_mcp/storage/db.py:152-169`、
`src/evoblue_video_mcp/web/app.py:993-1000`

`immediate_write_transaction` 明确处理了“取消落在 commit await、SQLite 已持久化”的情况：等待
commit verdict，持久化成功就保留写入并继续传播 `CancelledError`。但 settings PUT 捕获同一个
`CancelledError` 后无条件 `_restore_pointer()`。

实测让 `AsyncSession.commit()` 先真正提交新目录，再暂停并取消请求：最终 SQLite 中是
`new-reports`，`report-root.txt` 被恢复成 `old-reports`。数据库删除后的恢复会回到错误目录，可能
让现有报告历史看似丢失或重新索引另一目录。

需要让事务协调层暴露明确的 committed verdict，并按 verdict 补偿：

- 未提交：恢复旧 pointer、清理未采纳的新凭据槽；
- 已提交：保留新 pointer/新凭据槽、清理被替换槽；
- 取消仍向上传播，但不能丢失提交结果信息。

不能简单选择“取消一律恢复”或“取消一律不恢复”，两种选择都会在另一个取消时点制造分裂。
测试必须分别覆盖 commit 前取消和 durable commit 后取消，并同时断言 SQLite、pointer、通过
数据库 ref 读取到的凭据三者一致。

### [P2] 新 Key 写入后若报告指针阶段失败，新槽不会进入清理路径

位置：`src/evoblue_video_mcp/web/app.py:834-858`、`:902-943`

新 Key 在 843 行完成写入；报告指针读取或写入在 914-930 行失败时直接抛出 HTTPException。
`_cleanup_unadopted_fresh_slot` 在其后才定义，包裹数据库保存的 try 也尚未进入，因此确定不会
调度新槽清理。

实测保存新端点、新 Key 与新报告目录，并强制 pointer 写失败：API 返回 503、数据库仍绑定旧
槽，但凭据库同时留下 `sk-old` 和无人引用的 `sk-new`。这不会把 Key 发错端点，但会在失败请求
后留下 UI 不可见的秘密，与 prepare→commit→cleanup 合同不符。

应把 fresh-slot 清理覆盖到凭据写入之后的所有失败出口，包括 pointer 读取/写入失败和 DB
失败。补一条 pointer failure 后通过数据库 ref 验证旧 Key 可用，并断言新 Key/新 ref 已清理。

## 已确认通过的部分

- 新 Key 使用版本化槽；普通 DB 异常及迟到写入不再覆盖数据库仍引用的旧槽。
- 显式删除在普通成功/失败路径上采用先数据库解绑、后清理旧槽。
- 静态数据下，history 排除非 completed job、默认 total 探测、active 优先排序、platform
  大小写不敏感均已实现并有测试。
- B 站公开 CC / Cookie 字幕成功路径仍是明确登记的验收缺项。

## 本轮独立验证

- 相关回归：`130 passed`（Web/settings、settings/test、Bridge listing、listing helpers、
  migration、worker、diagnostics）。
- 双请求状态切换反例：稳定得到 `items=[dup, dup]`、`total=3`。
- durable commit 取消反例：稳定得到 SQLite=`new-reports`、pointer=`old-reports`。
- pointer failure 凭据反例：API 503 后凭据库保留旧槽和无人引用的新槽。
- `git diff --check` 另报告 `.gitignore`、`test_web_api.py`、`test_bridge_tools.py` 的 EOF 空行；
  属提交前清理项，不影响上述结论。
- 未使用真实 Key 重建或重跑发行包；本轮问题均为确定性的源码并发/失败路径，不依赖联网环境。
