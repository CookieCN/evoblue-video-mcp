# Test Strategy

## 反馈层

| 层级 | 目的 | 示例 |
|---|---|---|
| unit | 纯规则与 Schema | URL/文件名、状态转换、配置指纹、脱敏 |
| contract | 固定外部/跨模块合同 | 七 MCP JSON Schema、错误结构、Markdown v1、API |
| integration | 验证真实边界 | SQLite/FTS5、Worker 重启、文件原子写、Engine/Bridge |
| evals | 判断模型输出质量 | 摘要覆盖、幻觉、结构完整性、语言与营销模式 |
| end-to-end | 用户路径 | 首次设置、提交、重启、报告、索引重建、客户端握手 |

## P0 门禁

- `bash init.sh` Harness 与工具检查。
- Python 包导入。
- `.agents/feature_list.json` 与 `frontend/package.json` JSON 合法。
- Ruff、mypy、pytest。
- 前端 ESLint、Vitest、Vite build（依赖存在时）。

## 后续关键场景

- 同 URL/配置并发提交只执行一次。
- 每个 Job 阶段在副作用前后崩溃，重启后无永久卡住和重复最终报告。
- 取消在各阶段到达安全点；终止状态幂等。
- 恶意 URL、重定向、路径穿越、超大媒体、低磁盘、日志脱敏。
- 用户修改 Markdown 后重新分析不静默覆盖。
- 删除 SQLite 后由 Markdown 恢复历史与 FTS5 搜索。
- Bridge stdout 零日志；四客户端真实握手；工具调用在超时预算内返回。

## 环境缺失与代码失败

CI/本地报告必须分别标记：`PASS`、`FAIL_CODE`、`SKIP_ENVIRONMENT`。依赖/可执行文件未安装不应伪装为代码通过，也不应通过注释测试绕过。

