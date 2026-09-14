# 首位试用者修复评审

评审日期：2026-09-11。范围：工作区相对 `b902c51` 的修复代码、新增文件及 F0–F6 验收记录。
结论：需要修改后再验收。确认 4 项问题，其中 3 项 P1、1 项 P2；不能将当前状态视为修复包发布就绪。

本轮仅评审，未修改实现或用户配置。以下反例均在临时 SQLite、假凭据库和内存 HTTP transport 中执行，没有访问真实 Key、供应商 API 或正在运行的 Engine。

## R1 — P1：修改 Base URL 后，测试连接会向新站点发送已存 Key

位置：`src/evoblue_video_mcp/web/app.py:861–876`，`settings_test` 的存量凭据复用分支。

复现：库内保存 Provider=deepseek、Base URL=https://api.deepseek.com；测试请求只传相同 Provider 和 https://different.example，不填写 Key。捕获探测请求，发现它向新域名 `/models` 发出原已存 Key，并返回 ok。

```text
CHANGED_ORIGIN ok
url=https://different.example/models
credential_reused=True
```

原因：只比较 Provider 名称，没有比较凭据对应的目标 origin。用户修改自定义网关或误填地址后点测试，无需重新输入 Key 就会把旧凭据交给另一站点。不跟随重定向不能阻止这个初始请求。

修复要求：存量 Key 的复用至少同时匹配 Provider 与规范化 origin（scheme、hostname、有效 port）；跨 origin 要求用户显式提供用于新端点的凭据，不凭一个可编辑 Provider 字符串决定凭据归属。检查保存路径是否也能绕过此边界。

回归：同 origin 留空复用通过；只改域名、协议或端口拒绝复用且不发请求；跨 origin 显式输入新 Key 时使用新 Key；同 origin 合法路径更新按合同处理。

## R2 — P1：超时的旧凭据写入能覆盖已经成功的新保存

位置：`src/evoblue_video_mcp/web/app.py:672–691`，给 `to_thread(set_secret/delete_secret)` 增加 `wait_for` 的代码。

复现：将测试超时缩为 0.05 秒。第一次 set_secret 阻塞在线程 Event 上，返回 503；第二次保存新 Key 返回 200，凭据库已是新 Key；随后释放第一次线程，凭据被覆盖回第一次 Key。

```text
LATE_WRITE {
  first_status: 503,
  retry_status: 200,
  new_key_was_saved: True,
  old_write_overwrote_retry: True
}
```

原因：`wait_for` 取消 await 不会停止底层系统线程；超时后 settings_write_lock 已释放，旧写入与后续写入失去串行性。代码注释中的“重试会收敛”并不成立。删除操作也存在迟到删除新凭据的同类风险。

修复要求：HTTP 等待可有界，但实际凭据变更必须在同一受控串行通道内完成；未决旧操作不能越过后续成功结果。可采用持有真实操作所有权的串行队列/执行器，超时仅结束请求等待；未决期间后续变更必须排队或明确返回操作未决。进程内锁的生命周期不能仅绑定已超时的请求协程。

回归：用 Event 精确控制“旧写超时 → 新写请求 → 旧写结束”，确保不会出现新写已返回成功又被旧写覆盖；另测迟到 delete、请求取消、凭据库最终失败。读操作超时与写操作超时不能使用相同的状态一致性假设。

## R3 — P1：停靠时取消判断仍可读取旧 ORM 对象，留下悬挂取消

位置：`src/evoblue_video_mcp/storage/repository.py:257–273`，`advance_job` 的 cancel_landed 判断。

复现时序：

1. worker Session 领取 transcribing 任务，refresh 后 commit，仍持有 Job ORM 实例。
2. 独立 Session 调用 request_cancellation 并提交。
3. 原 Session 调用 advance_job，目标 waiting_for_model。
4. 新 Session 读回：状态 waiting_for_model，cancel_requested_at 非空。

```text
PARK_RACE waiting_for_model cancel_flag=True
```

原因：Session 使用 expire_on_commit=False；`_get` 的 SELECT 不会自动覆盖 identity map 内已有属性。Worker 虽然在阶段结束 refresh，但 refresh 与获取 IMMEDIATE 写锁之间仍有可提交取消的窗口；进入写事务之后的 SELECT 会复用旧实例，条件仍读到 None。

后果：任务不会及时 cancelled，新加的 resume 过滤又使它无法恢复；需要重启清扫或再次显式取消才能结束。这不是取消已被正确消费。

修复要求：在取得 IMMEDIATE 写锁后重新加载权威字段（显式 refresh/populate_existing），或将取消优先判定放入同一 SQL 更新表达式中。不要仅在写事务外再增加一次 refresh。

回归：将取消提交精确插在 Worker 的 refresh 之后、停靠写事务之前，使用两个独立 Session；确认直接 cancelled、租约释放，后续安装不复活。现有“handler 内先取消再返回”的测试不足以覆盖此窗口。

## R4 — P2：失败/取消任务超过 100 条后，分页仍返回空页

位置：`src/evoblue_video_mcp/mcp/listing.py:97–105`；调用方 `mcp/server.py::_list` 固定读取 limit=100、未下推 offset。

复现：服务端有 101 条 failed，Bridge 取到前 100 条且 total=101；调用 list_analysis_jobs(status=failed, offset=100, limit=20)。

```text
PAGE_101 total=101 returned=0
```

原因：新单源分支虽然采用服务端 total，实际分页仍切片第一次获取的 100 条。平台/关键词过滤也只针对这 100 条，窗口外匹配项不可发现；默认列表同样不能据此保证所有历史可访问。

修复要求：显式单状态查询将 limit/offset 和筛选下推 Engine，Bridge 不二次切片；默认双源列表采用有明确稳定排序、去重和精确计数的服务端查询或正确的有界取页策略。不要只把 100 改大。

回归：至少 101/205 条终态任务，连续翻页能完整覆盖且无重复/漏项；窗口外命中 platform/query 时可查；total 与同一过滤集合一致。

## 验收与证据缺口

- 本轮复跑相关测试 **87 passed**：settings_test_api、engine_recovery、worker_loop、job_identity_and_blockers、listing、engine_logging、model_manager。另执行上述 4 个独立反例，全部复现。因此现有测试通过不能关闭这些问题。
- 沙箱无法启动 venv 指向的用户目录解释器；测试使用本机 Python 3.12 配合项目现有 site-packages，在独立临时目录运行。未重建或重新验收发行包。最初测试启动遇到临时目录权限及子进程 pywin32 路径问题，调整本轮进程搜索路径和独立 basetemp 后上述 87 项全部通过；未修改项目运行环境。
- F6 记录明确两条真实链路均在 LLM 阶段失败，尚未证明修复包生成 Markdown、MCP 取报告和 FTS 搜索完整通过。原计划要求的 Bilibili 公开 CC 成功样本也不能由“登录墙 → 无字幕”替代。
- 现有模型安装证据使用预置已验证归档/partial。记录把从零联网下载留给 F6，但 F6 没有明确补齐这项证据，不能把 curl 可达等同于应用自身完整下载管线通过。
- 应把 F6 标记为未完成，并分别登记这些缺项；真实 Key/样本缺失时保留阻塞事实，不能缩写成“仅缺报告落盘一步”。无需为本次评审提供或展示真实 Key。
- DeepSeek 默认名不构成缺陷：2026-09-11 重新读取[官方调用文档](https://api-docs.deepseek.com/)，现行推荐名确为 `deepseek-flash`，`deepseek-v4-flash` 仍作为旧别名接受。执行者这项调整有官方依据。

## 交给执行 agent

先为 R1–R4 添加上述可复现失败测试，再逐项修复；保留现有已完成能力，禁止用扩大上限、放宽凭据边界或把测试预期改成当前错误行为通过检查。每项回报根因、修改、反例翻转结果。补齐发行验收缺项后再声明 F6 完成；未经 Wilson 指示不 push、不发布。
