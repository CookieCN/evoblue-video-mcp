# 首位试用者修复：第二轮评审

日期：2026-09-12。基线：当前未提交工作区、上一轮 R1–R4 修复及新音频重试改动。

结论：真实字幕与 ASR 报告链路有执行验收记录支撑；但代码评审仍不通过，R1 与 R4 各有一个可复现残留。R2 的迟到写入串行化、R3 的锁内读取取消标记，本轮未发现原反例残留。未修改实现、未使用真实凭据、未启动发行引擎或重新调用供应商。

## R1b — P1：setup=false 可以绕过凭据 origin 边界

位置：`src/evoblue_video_mcp/web/app.py:706–740`。

新 origin 检查位于 `if final_setup_completed` 内。已有配置保存着 A 站 Key 时：

1. `PUT /api/settings`：`{"setup_completed":false,"llm_base_url":"https://different.example"}`，不传 Key。
2. `POST /api/settings/test`：`{}`。
3. `PUT /api/settings`：`{"setup_completed":true}`。

使用临时 SQLite、假凭据库、内存 ASGI transport 和请求捕获函数实测：

```text
change=200
test=ok
reenable=200
old_key_sent=[('https://different.example/models', True)]
```

第一步更改了 Base URL 却保留原 credential_ref；第二步比较的是已经更新后的 URL，误认凭据属于新站点并发送旧 Key；第三步也能直接放行 Worker。是否完成 setup 是运行门禁，不能决定凭据安全边界是否生效。

修复要求：origin 绑定/失效处理必须独立于 setup_completed。修改 origin 未显式提供新 Key 时，要么拒绝该变更，要么原子解除配置对旧凭据的绑定；随后测试与重新完成 setup 均不得复用旧 Key。不要仅在测试端增加一次与“当前 URL”的比较。覆盖同请求关闭 setup 并改 URL、先关闭再修改、重新开启三种序列。

## R4b — P2：历史查询仍会截断，阈值变成 5000 条

位置：`src/evoblue_video_mcp/mcp/server.py:100–133`，`_fetch_bounded`。

显式 failed/cancelled 的分页下推已改善原问题；但 completed 与默认视图固定深取 50 页后仍返回正常成功，没有完整性或截断标志。

用假的 EngineClient 返回真实分页形状（每页 100 条、总数 5001），直接调用 `_list(status='completed', offset=5000, limit=20)`：

```text
ok=True
items=[]
total=5001
requests=50
```

第 5001 条无法访问；窗口外的筛选命中也会被丢弃。另外每次列 20 条都可能获取数千条，并给每个请求重新分配 10 秒超时，不能保证原工具总预算。

修复要求：completed 无关键词筛选直接向历史端点下推分页；其余场景实现基于 Engine 的统一分页/筛选/精确计数，或可证明完整且遵守总预算的取页算法。超过资源上限必须明确返回未完成/受限，不可用正常成功信封伪装完整页。禁止继续提高常数来替代分页修复。

## 验证与交付口径

- 本轮复跑 **100 passed**：settings_test_api、web_api、worker_loop、bridge_tools、listing、yt_dlp_adapter。两个新增反例均在上述现有测试全绿之外独立复现。
- 与前轮一样，使用系统 Python 3.12 配合项目现有 site-packages 和独立临时目录，未调整项目依赖、未读取或发送真实 Key。
- `docs/FIRST_USER_FEEDBACK_REPRO_LOG.md` 新增了真实闭环、从零下载记录，可关闭此前“没有 LLM 闭环证据”和“只有预置归档安装证据”的缺项。本轮未独立重跑这两项，应区分执行记录与本轮测试。
- B 站公开 CC/带登录字幕的成功路径仍未验收。ASR 成功不能证明该平台字幕提取成功，保留为明确缺项。
- `tmp-repro/verify_f6_llm_closed_loop.py` 的搜索断言只检查 total>=1，未断言命中对应的 yt_job/bili_job；两个样本都涉及同一首歌，先前的 YouTube 报告足以让 B 站搜索断言通过。应改为精确 job_id 断言，并核对每份 Markdown 的 analysis_id。当前记录称 total=2 是额外观测，不等于脚本已经锁定各自归属。
- “真实中文核心摘要”的表述也超出当前证据：用户贴出的摘录正文是英文，脚本只断言字符长度。若中文是验收要求，需核对正文语言；否则应准确表述为取回真实摘要。
- 新的音频 DownloadError 全部可重试属于有界重试取舍，本轮未发现必须阻塞的回归；“元数据成功”并不能证明后续音频一定可访问，文档应承认永久性故障也会耗尽重试预算，避免称其为精确错误分类。
- `.agents/progress.md` 的当前状态仍写 LLM 闭环缺失，与新记录不一致。收尾应同步实际状态；现存评审阻塞与 B 站字幕缺项不能被“14/14 全过”覆盖。

交接：先添加 R1b/R4b 反例测试并修复，再补强闭环脚本的任务归属断言、同步状态。未经 Wilson 指示不 commit、push 或发布。
