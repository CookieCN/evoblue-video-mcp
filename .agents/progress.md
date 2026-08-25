# 项目进度 — 2026-08-25

## 当前状态

P0 已完成。P1 核心已就绪：SQLite Job 模型、版本化迁移、状态机转换规则、单 Worker 租约领取与崩溃恢复、Worker 执行循环骨架与 Engine 启动恢复。尚未实现 WebUI 与真实视频 Pipeline。

## 最近完成

- [x] 统一 Python 包名 `evoblue_video_hub` → `evoblue_video_mcp`，对齐项目名 `evoblue-video-mcp`。
- [x] 新增状态机转换规则 `jobs/transitions.py`：线性主链 + retry_wait 重入 + 终止态无出边，非法转换抛 `TransitionError`。
- [x] 新增 SQLite 持久层 `storage/`：`Job` ORM 模型、版本化迁移（`schema_migrations` + `SCHEMA_VERSION=1`）、异步 engine（WAL + busy timeout）。
- [x] 新增单 Worker 租约 `storage/repository.py`：原子 compare-and-swap 领取、乐观锁、租约过期接管、终止态保护、恢复扫描。
- [x] 修复审查发现的 4 个 P1 缺陷：`advance_job` 增加租约 CAS 校验（旧 Worker 无法推进）、`max_attempts` 生效并原子转 failed、`recover_stale_jobs` 原子化（不覆盖新租约）、Repository 返回不依赖 `expire_on_commit=False`。
- [x] 新增 Worker 执行循环骨架 `runtime/worker.py`：`claim → 阶段 handler 注入 → 连续推进到终止/retry_wait/failed`，阶段处理器可插拔（P2 填真实 pipeline）。
- [x] 新增 Engine 启动恢复 `runtime/engine.py`：`recover_on_startup` 释放过期租约 + 耗尽重试原子转 failed。
- [x] 覆盖状态转换、并发领取、崩溃恢复、终止态、租约易主、重试上限、Worker 循环与启动恢复的单元/集成测试（45 passed）。

## 待做（优先级排序）

| # | 事项 | 优先级 | 阶段 |
|---|---|---|---|
| 1 | 建立 WebUI 路由与首次设置状态机 | P1 | P1 |
| 2 | 实现 YouTube/Bilibili 字幕获取适配器 | P2 | P2 |
| 3 | 统一 MCP 返回 Envelope：`docs/MCP_TOOLS.md` 的 `ok` 字段与 Pydantic 输出模型不一致 | P2 | P4 |

## 已知问题

- 后端依赖与验证已完成；当前执行环境拒绝连接 npm registry，前端依赖安装及 lint/test/build 标记为 `SKIP_ENVIRONMENT`。
- `package-lock.json` 尚未生成，CI 暂不切回 `npm ci`。
- MCP Python SDK v2 为当前稳定线，真正接入放在 P4；P0 仅定义与 SDK 解耦的业务 Schema。

## 阶段路线图

| 阶段 | 状态 | 目标 | 前置依赖 | 验收标准 |
|---|---|---|---|---|
| P0 | 完成 | Harness、边界、合同、最小骨架 | 无 | 合同齐全，骨架可导入，基础验证通过或明确环境缺失 |
| P1 | 进行中 | Local Engine、SQLite、Worker、WebUI 框架 | P0 | Engine 可恢复任务，WebUI 可观察任务与设置状态 |
| P2 | 计划 | YouTube/Bilibili 字幕分析闭环 | P1 | 两平台有字幕视频可生成报告，失败有稳定错误码 |
| P3 | 计划 | 历史、Markdown、FTS5、索引重建 | P2 | 删除 SQLite 后可从 Markdown 重建可搜索索引 |
| P4 | 计划 | STDIO MCP Bridge 和四客户端 | P1-P3 | 七工具合同测试通过，多客户端不重复执行 |
| P5 | 计划 | WebUI MCP 自动配置与真实握手 | P4 | 配置可备份、合并、验证和恢复 |
| P6 | 计划 | Faster-Whisper、模型管理和恢复 | P2 | 无字幕视频可选本地 ASR，重启可恢复 |
| P7 | 计划 | Windows 安装器和 GitHub Release | P1-P6 | 干净 Windows 环境可安装、升级、卸载 |
| P8 | 计划 | 公开测试和 Harness 加固 | P7 | 关键失败模式有自动化护栏与审计记录 |

各阶段的功能范围、非目标、风险与回滚方式见 `docs/PRD.md`。
