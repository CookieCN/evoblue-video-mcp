# 第一个适用者反馈修复：第七轮代码评审

评审日期：2026-09-14

## 结论

第六轮点名的两个基础反例已经关闭：显式 completed/failed 视图不再把缺失顶层字段伪装成
空页，统一视图也开始校验 totals、窗口容量和两类 item 的状态。

**本轮仍有 2 个 P2，需要在提交前修复。** 当前实现叫作“完整、严格 REST 合同验证”，但实际
仍是手写的宽松投影；类型错误会被主动转换，统一页也没有验证 item 是否位于计数所确定的正确
分段。两种情况都会把畸形 Engine 数据作为 `ok:true` 返回。

## Findings

### [P2] item 解析继续强制转换错误类型，并接受 REST 必填字段缺失

位置：`src/evoblue_video_mcp/mcp/listing.py:38-62`、
`src/evoblue_video_mcp/mcp/listing.py:111-151`

`_job_item`/`_history_item` 对 `job_id/title/platform/url` 使用 `str(...)`，对 progress 使用
`int(...)`；这不是严格验证，而是在替 Engine 修数据。与此同时，Engine 的 REST 模型要求 jobs
item 带 `progress/created_at`，history item 带完整 HistoryItem 字段，unified item 也要求
`progress`，当前解析器只要求转换 MCP 输出时碰巧访问到的少数字段。

通过完整 MCP 工具调用实测，以下畸形 HTTP 200 全部返回 `ok:true`：

- completed item 的 `job_id=123` 被改写成字符串 `"123"`；
- failed item 的 `job_id=123、progress=true、title=["bad"]` 被改写成
  `job_id="123"、progress=1、title="['bad']"`；
- unified job 缺少 REST 必填 `progress`，被输出为 `progress=null`。

应在投影前用 wire-level 模型验证每个数据源的必填字段与类型；建议使用与 Engine REST schema
对应的内部 Pydantic 模型并以 strict 模式验证，再显式映射到 MCP schema。允许忽略未来新增的
字段，但不得转换错误类型或放宽当前必填字段。`limit/offset` 回显也要显式拒绝 bool（Python 中
`True == 1`、`False == 0`，当前等值比较会误收）。任何验证错误继续统一降级
`BRIDGE_INTERNAL`。

现有 `test_page_rejects_malformed_items` 中多个 unified 夹具同时缺少
`jobs_total/history_total`，所以在 `_strict_window`/segment-total 检查就提前失败，并未执行注释
声称的 missing/invalid item 字段路径；Bridge 的 missing-status 测试也有同样问题。修测试时须先
构造其余字段完全合法的基线，只改变被测字段。

### [P2] unified totals 正确时仍可返回错误分段，破坏全局分页顺序

位置：`src/evoblue_video_mcp/mcp/listing.py:177-219`

当前只验证 `total == jobs_total + history_total`，以及 `kind=job` 不是 completed、
`kind=history` 是 completed；没有把每一行的全局位置与分段计数关联。若
`jobs_total=1、history_total=1、offset=0、limit=2`，Engine 返回：

```json
[
  {"kind":"history","job_id":"h1","status":"completed"},
  {"kind":"job","job_id":"j1","status":"failed"}
]
```

现有所有不变量检查都通过，MCP 返回 `ok:true`，但冻结合同规定第一页必须先取 jobs 段再取
history 段。更一般地，Engine 可以在 jobs 应占的位置塞入合法 history item，造成客户端看到
错误页、遗漏任务或跨页重复，而 totals 和页容量仍完全一致。

应按每个返回项的全局下标 `offset + index` 推导期望 kind：小于 `jobs_total` 必须为 job，否则
必须为 history；这同时覆盖纯 jobs 页、跨段页、纯 history 页和深 offset。补一条跨段反序反例，
并至少覆盖 `offset < jobs_total` 的跨段页与 `offset >= jobs_total` 的 history-only 页。错误仍降级
为普通 `BRIDGE_INTERNAL` 信封。

## 已确认通过的部分

- completed/failed 收到缺失 items/total 的响应时均返回 `BRIDGE_INTERNAL`。
- 非 object item 在三个入口中均不再被静默丢弃。
- total 分段和、请求窗口回显、页容量、completed job 与 failed history 的基础检查已接入。
- 三个分支均捕获 `EngineListFormatError` 并通过 `_validated` 返回 `isError=false` 的冻结错误信封。
- `_list` 说明已改成单一 unified 请求、单一 SQLite 快照。

## 本轮独立验证

- 相关回归：`134 passed`（listing、Bridge、Web API、设置/凭据与 immediate transaction）。
- completed numeric job_id：错误返回 `ok:true` 并改写为字符串，FAIL。
- failed 错误类型字段：错误返回 `ok:true` 并强制转换，FAIL。
- unified 缺失必填 progress：错误返回 `ok:true, progress:null`，FAIL。
- unified totals 正确但 history/job 反序：错误返回 `ok:true`，FAIL。
- `git diff --check` 无空白错误；未使用真实 Key，未重建或重跑联网发行包。
