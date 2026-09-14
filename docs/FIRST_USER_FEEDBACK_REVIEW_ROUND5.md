# 第一个适用者反馈修复：第五轮代码评审

评审日期：2026-09-14

## 结论

第四轮的两个 P1 已经关闭：统一端点确实在同一 SQLite/WAL 读快照内完成两段读取；
`on_commit` 在真实 durable-commit 取消窗口中也能阻止错误 pointer 补偿。

**本轮仍有 2 个 P2，需要在提交前修复。** 另有临时文件和空行噪声需要清理。

## Findings

### [P2] `commit_state=False` 的取消已经证明未提交，却仍保留未引用的新 Key 槽

位置：`src/evoblue_video_mcp/web/app.py:1112-1124`

引入 `on_commit` 后，所有 durable-success 路径都会在传播 `CancelledError` 前把
`commit_state["committed"]` 置为 true。因此外层捕获取消且值仍为 false 时，数据库没有采纳
fresh slot；此时清理它是安全且确定的，不再是注释所说的“likely happened”。

当前 false 分支只恢复 pointer，没有调用 `_cleanup_unadopted_fresh_slot()`。实测：

1. 数据库与凭据库先保存旧端点、旧版本化槽、`sk-old`；
2. PUT 新端点和 `sk-new`，让新槽写入成功；
3. 在 `save_app_settings` 提交前取消请求；
4. 数据库仍引用旧槽，但凭据库同时留下 `sk-old`、无人引用的 `sk-new`。

应在 `commit_state=False` 的取消分支同时恢复 pointer 并排队清理 fresh slot；true 分支继续保留
新槽并清理 replaced slots。测试必须使用真实 task cancellation，在数据库 body/commit 前阻塞，
不能用“事务结束后人工抛 CancelledError”的 wrapper 替代。

### [P2] 统一列表的畸形 Engine 响应会被伪装成成功空页或升级成协议异常

位置：`src/evoblue_video_mcp/mcp/server.py:294-303`、
`src/evoblue_video_mcp/mcp/listing.py:128-147`

Bridge 的冻结合同要求 Engine/Bridge 版本偏差或畸形成功载荷降级为普通工具结果
`ok:false / BRIDGE_INTERNAL`。但 `unified_page` 对必填字段使用宽松默认：

- Engine 返回 HTTP 200 `{"unexpected":"shape"}` 时，当前返回
  `ok:true, items:[], total:0`，静默隐藏全部任务和历史；
- 返回带 item 但缺少 `status` 时，`_job_item` 抛 `KeyError`，`call_tool` 升级成协议级
  `ToolError`，绕过 `_validated` 的 `ValidationError` 降级。

需要先用严格内部模型验证 unified response 的顶层必填字段、kind 判别和每类 item 字段，再做
转换；转换期间的 `ValidationError`/`KeyError`/`ValueError` 统一映射为 `BRIDGE_INTERNAL`，日志只
记录工具名。不要把缺失 `items`/`total` 当空集合/零总数。

至少补两条反例：缺失顶层字段、job item 缺失/非法 status；两者均须 `isError=false` 且返回
`BRIDGE_INTERNAL`。

## 已确认通过的部分

- `GET /api/jobs/unified` 的 session 不在两段读取之间 commit；首个 SELECT 固定 WAL 快照，
  并发完成提交不会在 history 段重新收编同一 job。
- Bridge 默认视图只调用统一端点一次；静态与段间状态切换测试均通过。
- 真实模拟“SQLite 已 durable commit、commit coroutine 尚未返回时取消”，最终数据库与
  `report-root.txt` 都指向新目录。
- pointer 读写普通失败会进入 fresh-slot 清理路径。
- 版本化凭据槽的普通成功、DB 失败、迟到写入和删除路径保持上一轮修复语义。

## 提交前清理

- 仓库根目录存在未跟踪的 `tmp-block1.txt`，内容是已废弃的双请求 Bridge 测试片段，应删除。
- `git diff --check` 报告 `.gitignore:43` 与 `tests/integration/test_web_api.py:1549` 多余 EOF 空行。
- `mcp/listing.py` 顶部说明及 `merge_default_pages`/`merge_job_pages` 仍描述、测试已经退出生产
  路径的双请求合并。建议删除死代码和对应镜像测试，或至少更新说明，避免测试数量掩盖真实
  `unified_page` 覆盖不足。

## 本轮独立验证

- 相关回归：`128 passed`（Web API、索引/指针、immediate transaction、Bridge、listing）。
- 真实 durable-commit 取消：SQLite 与 pointer 均为新目录，PASS。
- 提交前取消：数据库旧 ref 未变，但凭据库残留 `sk-new`，FAIL。
- unified 畸形空载荷：错误返回 `ok:true, total=0`，FAIL。
- unified item 缺 status：抛协议级 `ToolError`，FAIL。
- 未使用真实 Key 重建或重跑联网发行包；本轮问题均为确定性本地失败路径。
