# Hardening Matrix (P8)

Status: 2026-09-07（P8-001）。每行把一个真实失败模式钉到「护栏测试 id + 用户可见
行为 + 恢复路径」；新失败模式先加测试再入表（矩阵只引用真实存在的护栏）。
行为变更必须连同行内测试一起改，漂移由评审把关。

| # | 失败模式 | 用户可见行为 | 恢复路径 | 护栏（测试 / 脚本） |
|---|---|---|---|---|
| 1 | 端口被其他程序占用时启动 | 退出码 4 + 中文指引（换 `EVOBLUE_ENGINE_PORT`），无栈回滚 | 用户换端口重启 | `tests/integration/test_fault_injection.py::test_port_occupied_at_boot_exits_4_with_guidance`（FI-1） |
| 2 | 重复启动 Engine | 退出码 3 + 「已在运行，请使用已运行实例」 | 无需操作，直接用运行中实例 | `tests/integration/test_fault_injection.py::test_second_instance_message_precedes_any_server_setup`（FI-2）、`tests/integration/test_single_instance.py`、`scripts/verify_p7_acceptance.py` 步骤 3 |
| 3 | LLM 限流 / 网络超时 | 任务转 `retry_wait` 自动重试（retryable 稳定码） | 自动重试；多次耗尽转 failed | `tests/unit/test_llm_http.py`（429/timeout retryable）、`tests/integration/test_worker_loop.py::test_worker_transient_failure_enters_retry_wait` |
| 4 | LLM 认证失败 / 5xx | `failed` + 非重试稳定码（`LLM_AUTH_FAILED` 等） | 用户在 WebUI 换 Key 后重新提交 | `tests/unit/test_llm_http.py` |
| 5 | 平台字幕缺失 | `failed` + `SUBTITLE_UNAVAILABLE` | 安装本地语音模型走 ASR 转写 | `tests/integration/test_pipeline.py::test_failure_maps_error_detail` |
| 6 | 报告目录不可写（磁盘满/只读） | 任务 `failed`、错误信息脱敏（不泄 OS 错误与路径） | 修复目录后重新提交；诊断盘空间/报告目录检查给预警 | `tests/integration/test_fault_injection.py::test_report_write_failure_fails_job_sanitized`（FI-3）、diagnostics `disk_space`/`report_directory` |
| 7 | ASR 模型文件损坏 | Engine 照常启动，该 Tier 未注册 + 稳定错误码日志 | WebUI 重装模型 | `scripts/verify_release.py` 步骤 3（需真实模型）、ASR-3 Provider 加载隔离测试 |
| 8 | SQLite 丢失 / 损坏 | 历史与搜索自动从 Markdown 重建 | 全自动（启动对账/手动 rebuild API） | `tests/integration/test_index_rebuild.py`（删库自愈端到端） |
| 9 | 初始设置未完成就提交任务 | 首页横幅「任务不会开始」+ 诊断 `worker_runtime` warning（`setup_incomplete`） | 完成设置后队列任务自动开跑 | `tests/unit/test_diagnostics.py::test_worker_runtime_mirrors_the_claim_gate`、`frontend/src/App.test.jsx` 横幅测试 |
| 10 | 浏览器丢失本机令牌（清存储/换浏览器） | WebUI 显示「请粘贴本机访问令牌」门页 | 复制数据目录 `local_token` 文件内容粘贴 | `frontend/src/App.test.jsx`（401 门页）、`tests/integration/test_web_token_bootstrap.py` |
| 11 | MCP 客户端配置文件被外部改坏 | fail-closed：稳定码（`CONFIG_UNSUPPORTED` 等），绝不写入 | WebUI 备份恢复 | `tests/contract/test_client_write_contract.py`、P5 评审修复轮回归测试（坏 JSON/dotted-key/满架恢复） |
| 12 | 模型下载中断 | 进度持久化，恢复网络后断点续传 | 自动续传或取消后重试 | ASR-2 续传测试（真实中断/坏 partial 回退） |
| 13 | 未签名程序被 SmartScreen/杀软拦截 | Release notes 与 SUPPORT 的放行指引 | 「更多信息 → 仍要运行」/加白名单 | `docs/SUPPORT.md`（人工清单项，无自动化） |
| 14 | MCP 客户端先于 Engine 启动 | 工具返回 `ENGINE_NOT_READY`（retryable）+ 启动指引 | 启动 EvoBlue（自启动已默认兜底） | `tests/integration/test_bridge_stdio.py` 离线用例、ADR 0004 |

## 维护规则

- 新增失败模式：先写护栏测试，再在表里加行；删测试必须连行一起删。
- 「用户可见行为」列是合同的一部分：改文案/退出码/稳定码必须同步
  `docs/INSTALLER_RELEASE_CONTRACT.md` §3 或相应合同，并跑对应漂移测试。
