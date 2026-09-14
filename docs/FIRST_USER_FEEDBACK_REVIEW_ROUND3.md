# 第一个适用者反馈修复：第三轮代码评审

评审日期：2026-09-14

## 结论

**本轮不能关闭。** R1b 的正常路径与 R4b 的深分页单源路径已经翻转，但新增实现仍有
2 个 P1 阻断项和 2 个 P2 合同回归。现有相关测试 103 项全部通过，下面的最小反例仍可
稳定复现，说明需要补失败路径和跨页不变量测试。

## Findings

### [P1] Keyring 先改、SQLite 后提交，503 后旧端点可能使用新站点的 Key

位置：`src/evoblue_video_mcp/web/app.py:781-807`、`:870-931`

当前 PUT 先用 `llm:provider` 这个可复用槽执行 `set_secret` / `delete_secret`，随后才提交
`llm_credential_ref` 与 `llm_credential_origin`。数据库异常补偿只恢复报告目录指针，没有
恢复凭据槽。更直接的失败路径是：凭据写入超时后请求立即返回 503，但串行 writer 明确会
让操作继续执行；如果用户没有再发第二个请求，迟到的新 Key 会覆盖旧槽，而 SQLite 仍指向
旧端点和旧槽。

实测反例：

1. 保存端点 A + `sk-old-A`，数据库记录 `ref=llm:deepseek, origin=A`；
2. PUT 端点 B + `sk-new-B`；让 Keyring 写成功后强制数据库保存失败；
3. API 返回 503；数据库仍为 A，但 `llm:deepseek` 已变成 `sk-new-B`。

这会把 B 的凭据发送给 A，也会丢失 A 的凭据。删除路径同理：Keyring 删除成功、数据库
失败后，旧配置仍引用一个已被删除的槽。

建议不要继续给已被数据库引用的槽做原地覆盖。用不可变/版本化引用完成 prepare → commit：

1. 新 Key 写入新的唯一引用；
2. 写入确认后，SQLite 原子提交新引用与 origin；
3. 提交成功后再尽力清理旧引用；失败或超时只留下未引用的新槽，旧配置仍可安全运行；
4. 删除时先原子解除数据库绑定，再清理旧槽。

必须补三条反例测试：迟到写入后数据库仍绑定旧值、Keyring 成功而 DB 失败、删除成功而
DB 失败。断言应通过数据库里的 ref 实际读取 secret，不能只检查 PUT 响应。

### [P1] 默认分页没有一个可精确分页的集合，`total` 漏数且同一 job 可跨页重复

位置：`src/evoblue_video_mcp/mcp/server.py:288-322`、
`src/evoblue_video_mcp/mcp/listing.py:93-125`

当 `/api/jobs` 返回满页时，代码不请求 `/api/history`，`history_body=None` 使
`history_total=0`。例如任务段 total=2、历史段 total=5001、`limit=1, offset=0`，当前返回
`total=2`，客户端会在任务段结束时停止分页，5001 条历史对默认视图不可达。

“只做页内去重”也不能支持全局 offset。报告索引会在 `IndexingHandler` 内先提交，job 再由
worker 的下一笔事务推进到 `completed`；两步之间失败时，同一 job 合法地同时存在于
非 completed 任务段和历史段。实测 page 0 返回失败任务 `dup`，page 1 又返回历史 `dup`，
而两页的 total 分别为 1 和 3。`merge_default_pages` 中“跨页重复不可能”的注释不成立。

建议让 Engine 提供一个统一的列表端点，在同一 SQL 快照中完成分段排序、全局去重、count、
offset 和 limit。若保留双端点，必须先让两个集合在服务端严格不相交（历史段排除所有
non-completed job，保持“任务行胜出”），并在每一页都取得两个段的精确 total；仅凭当前页
无法计算全局去重后的 total。

必须补：第一页完全落在任务段但仍报告完整 total、边界前后连续翻页无重无漏、索引已提交
但 job 仍为 indexing/failed 的真实数据库反例、过滤后的全局 total。

### [P2] 默认排序不再保证运行中任务位于 failed/cancelled 之前

位置：`src/evoblue_video_mcp/storage/repository.py:464-485`

合同规定“运行中 → failed/cancelled → 报告历史”。当前 `status_group=non_completed` 把运行中
与失败/取消混在一个集合，只按 `created_at DESC` 排序。一个较新的失败任务会排在较旧但仍
运行的任务之前，改变 MCP 已冻结的用户可见顺序。

统一端点或 jobs 查询应在 SQL 中先按 active/terminal 分组排序，再按稳定的时间与唯一键
排序，并增加“新失败 + 旧运行中”的反例。

### [P2] completed/history 的 platform 过滤从大小写不敏感退化为大小写敏感

位置：`src/evoblue_video_mcp/storage/report_repository.py:760-762`

MCP 合同写明 platform 为不区分大小写精确匹配，jobs 端点也使用 `LOWER`；history 新下推
仍使用 `platform = :platform`。因此 `status=completed, platform=YOUTUBE` 返回 0，默认视图
两个数据源对同一过滤值的语义也不同。

在边界规范化为小写，或在查询中使用 `LOWER(platform) = LOWER(:platform)`，并补 completed
及默认视图的大小写反例。

## 已确认通过的部分

- R1b 的三条正常交互序列已有测试覆盖：同请求关闭并改地址、先关闭再改地址、重新开启。
- `status=completed, offset=5000` 已真正下推到 history，单源第 5001 条可一请求取回。
- 闭环脚本先清空独立数据目录，两个搜索断言均检查精确 job_id；修正后的 YAML 引号正则能
  读出两份报告的 `analysis_id`。这部分可以接受。后续可把断言再收紧为“恰好两份文件且每份
  都解析成功”，避免额外损坏报告被集合去重掩盖，但不作为本轮阻断项。
- B 站公开 CC / 登录 Cookie 字幕成功路径仍是明确登记的验收缺项；ASR 成功不能替代它。

## 本轮独立验证

- 相关回归：`103 passed`（settings、settings/test、Web API、Bridge listing、migration、worker）。
- `git diff --check`：仅 `.gitignore:43` 多一个 EOF 空行。
- 未使用真实 Key 重跑联网闭环；本轮接受执行记录中的发行包结果，但它不能覆盖上述确定性
  失败路径。
