# Changelog

## [Unreleased]

### Added

- 统一 Python 包名 `evoblue_video_hub` → `evoblue_video_mcp`，对齐项目名 `evoblue-video-mcp`。
- 新增 SQLite Job 模型（`storage/models.py`）、版本化迁移（`schema_migrations` + `SCHEMA_VERSION`）。
- 新增状态机转换规则（`jobs/transitions.py`），非法转换抛 `TransitionError`。
- 新增单 Worker 租约闭环（`storage/repository.py`）：原子 compare-and-swap 领取、乐观锁、租约过期接管与恢复。
- 新增覆盖状态转换、并发领取、崩溃恢复、终止态保护与迁移幂等的测试。
- 新增 Worker 执行循环骨架（`runtime/worker.py`）：`StageOutcome`/`StageHandler` 协议与 `run_worker_once` 连续推进。
- 新增 Engine 启动恢复（`runtime/engine.py`）：`recover_on_startup` 释放过期租约并失败耗尽重试。
- 新增 AppSettings 模型与迁移 v2（精确 per-table 迁移），`list_jobs` 与设置 get/save repository。
- 新增 WebUI 后端 API（`/api/jobs` 列表/详情、`/api/settings` GET/PUT）与前端路由骨架（任务列表 + 首次设置 + Vite loopback 代理）。

### Changed

- 后端验证从 `evoblue_video_hub` 迁移到 `evoblue_video_mcp` 包路径。

### Fixed

- 修复租约易主后旧 Worker 仍能推进状态的问题：`advance_job` 现校验 `lease_owner` + 租约有效性，失败抛 `LeaseLostError`。
- 修复 `max_attempts` 不生效的问题：领取与查询均约束 `attempt < max_attempts`，耗尽原子转 `failed`（`MAX_ATTEMPTS_EXCEEDED`）。
- 修复恢复扫描「先读后写」竞态：`recover_stale_jobs` 改为带条件的原子 UPDATE。
- 修复 Repository 返回依赖 `expire_on_commit=False` 的问题：写后重新读取，默认 Session 下不再 `MissingGreenlet`。
