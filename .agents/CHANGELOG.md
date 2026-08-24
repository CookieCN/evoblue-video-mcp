# Changelog

## [Unreleased]

### Added

- 统一 Python 包名 `evoblue_video_hub` → `evoblue_video_mcp`，对齐项目名 `evoblue-video-mcp`。
- 新增 SQLite Job 模型（`storage/models.py`）、版本化迁移（`schema_migrations` + `SCHEMA_VERSION`）。
- 新增状态机转换规则（`jobs/transitions.py`），非法转换抛 `TransitionError`。
- 新增单 Worker 租约闭环（`storage/repository.py`）：原子 compare-and-swap 领取、乐观锁、租约过期接管与恢复。
- 新增覆盖状态转换、并发领取、崩溃恢复、终止态保护与迁移幂等的测试。

### Changed

- 后端验证从 `evoblue_video_hub` 迁移到 `evoblue_video_mcp` 包路径。
