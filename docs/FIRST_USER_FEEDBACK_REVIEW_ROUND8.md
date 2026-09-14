# 第一个适用者反馈修复：第八轮代码评审

评审日期：2026-09-14

## 结论

**第七轮的两个 P2 已关闭，本轮无新增 finding，可以提交。**

三个列表来源现在先以 strict wire model 验证 Engine 响应，再投影为 MCP 输出；错误类型、当前
必填字段缺失、分页回显异常、计数矛盾、页容量异常、状态归属错误和全局分段错位均进入同一个
`EngineListFormatError` 边界，并降级为 `ok:false / BRIDGE_INTERNAL`、`isError=false`。

## 已确认

- `_WireJobRow`、`_WireHistoryRow`、`_WireUnifiedRow` 与对应 Engine REST item schema 的字段集合
  自动比对均为 `missing=[] / extra=[]`。
- wire model 使用 `strict=True`，不再转换数字 job_id、布尔 progress 或错误类型 title；
  `extra="ignore"` 允许未来 Engine 添加字段，不破坏旧 Bridge。
- `limit/offset` 回显显式排除 bool；wire 合法但超过 MCP 上限的值也在投影边界内降级。
- unified 逐行使用 `offset + index < jobs_total` 推导预期 kind；纯 jobs 页、跨段页、纯 history
  页、深 offset 和越界空页公式均正确。
- 三个列表分支均捕获 `EngineListFormatError`，没有协议级异常或成功空页旁路。
- 上轮指出的畸形 item 测试已改为从完整合法夹具出发，只改变被测字段，确实到达目标分支。

## 独立验证

- 相关回归：`139 passed`。
- 项目虚拟环境完整后端 pytest：100% 完成，exit 0。
- Ruff：`All checks passed!`。
- mypy：`Success: no issues found in 103 source files`。
- 完整 MCP 调用探测：合法深 history 页、跨段页、越界空页均成功；错误 wire 类型、反序分段、
  MCP progress 越界均返回 `BRIDGE_INTERNAL` 且 `isError=false`。
- `git diff --check` 无空白错误。

仍登记的 B 站公开 CC 字幕样本是发行验收环境缺项，不是本轮代码 finding；提交不需要等待该
样本，正式发布前仍应按现有清单补验或明确保留该限制。
