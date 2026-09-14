# 第一个适用者反馈修复：第六轮代码评审

评审日期：2026-09-14

## 结论

第五轮的 R12 凭据清理反例已经关闭，统一默认视图的两个 R13 样例也已翻转；临时文件、EOF
空行和双请求合并死代码均已清理。

**本轮仍有 2 个 P2，需要在提交前修复。** 两项属于同一个根问题：
`list_analysis_jobs` 对 Engine 成功载荷没有一套覆盖所有分支、完整响应语义的统一验证边界。

## Findings

### [P2] R13 只保护默认视图，显式状态分支仍把畸形响应伪装成成功空页

位置：`src/evoblue_video_mcp/mcp/listing.py:48-90`、
`src/evoblue_video_mcp/mcp/server.py:267-287`

`UnifiedResponseFormatError` 只包住 `status=null` 的 `/api/jobs/unified` 分支。显式
`status=completed` 仍走 `completed_page`，其他显式状态仍走 `single_source_page`；二者继续：

- 用 `body.get("items", [])` 把缺失 items 当作空集合；
- 丢弃所有非 object item；
- 用 `body.get("total") or 0` 把缺失 total 当作零。

实际通过完整 MCP 工具调用复现：Engine 对 `status=completed` 或 `status=failed` 返回 HTTP 200
`{"unexpected":"shape"}`，两次都得到 `ok:true, items:[], total:0, isError=false`。这仍会让 AI
客户端把 Engine/Bridge 版本错配或 Engine bug 误判为“当前没有任务”。

应给三种数据源响应使用同一个严格验证入口，或分别建立严格的 jobs/history/unified 内部模型；
任何必填字段缺失、字段类型错误、item 非 object 或 item 转换失败，均返回
`ok:false / BRIDGE_INTERNAL`，`isError=false`。至少增加 completed、failed 两条完整工具反例，另加
非 object item 的反例，防止 `_dict_items` 的静默丢弃行为回归。

### [P2] `unified_page` 的“严格验证”没有验证统一响应的计数、窗口和分段不变量

位置：`src/evoblue_video_mcp/mcp/listing.py:99-146`

Engine 的 `UnifiedJobListResponse` 还要求 `jobs_total`、`history_total`、`limit`、`offset`，冻结合同
要求 `total == jobs_total + history_total`，job 段只含非 completed 状态，history 段呈现 completed。
当前 Bridge 只读取 `items` 和 `total`，并按 `kind` 强制改写 history 状态，导致以下畸形 200
响应继续 `ok:true`：

- 请求第一页时 `items=[]、total=7`：返回“空页但还有 7 条”，仍能静默隐藏数据；
- history item 声明 `status=failed`：Bridge 将它改写为 `completed`，掩盖上游违约；
- job item 声明 `status=completed`：被接受，破坏统一端点两段严格不相交的语义；
- 缺少 `jobs_total/history_total/limit/offset`，或三项 total 不一致：完全不检查。

应先按完整 REST 响应合同验证，再转换 MCP 输出，并增加跨字段检查：响应窗口须与请求一致、
`total` 须等于两个分段计数之和、当前页 item 数量须符合 `limit/offset/total`、history item
必须为 completed、job item 不得为 completed。任一违约统一降级 `BRIDGE_INTERNAL`，不要纠正或
忽略 Engine 的错误数据。

## 已确认通过的部分

- R12：提交前真实 task cancellation 会恢复 pointer，并异步清理未被数据库采纳的 fresh slot。
- R13 已给出的两个默认视图样例：缺顶层 items/total、job 缺失或非法 status，均降级
  `BRIDGE_INTERNAL`。
- `tmp-block1.txt` 已删除；`git diff --check` 无空白错误；双请求 merge helper 已从生产代码删除。
- 第四轮的一致快照、durable commit verdict、pointer 失败清理语义在相关回归中保持通过。

## 提交前补充清理

`src/evoblue_video_mcp/mcp/server.py:246-254` 的函数说明仍把默认视图描述为“两段、两请求”，
与当前单一 `/api/jobs/unified` 请求不符；修复上述验证边界时一并改成单请求、单快照合同。

## 本轮独立验证

- 相关回归：`128 passed`（Web API、索引/指针、immediate transaction、Bridge、listing）。
- completed 畸形成功载荷：错误返回 `ok:true, total=0`，FAIL。
- failed 畸形成功载荷：错误返回 `ok:true, total=0`，FAIL。
- unified 空第一页但 positive total：错误返回 `ok:true`，FAIL。
- unified history failed 被改写为 completed、completed job 被接受：FAIL。
- 未使用真实 Key，未重建或重跑联网发行包；本轮问题均为确定性 Bridge 合同问题。
