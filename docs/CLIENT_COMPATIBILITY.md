# Client Compatibility Contract

## 共同行为

- STDIO Transport；Bridge stdout 只承载 MCP 协议，日志写 stderr/文件。
- `submit_video_analysis` 应快速返回，客户端无需保持一次长调用。
- 配置修改前备份、结构化合并、保留其他服务器，之后进行真实握手。
- 自动化不能确认成功时必须返回“未验证”，不得仅因写入文件就显示成功。
- 写入、备份、验证与恢复的执行细节以 `docs/CLIENT_CONFIG_WRITE_CONTRACT.md`（P5 冻结）为准。

## Bridge 启动合同（P4 冻结）

- 主形态：`command = <venv>\Scripts\python.exe`（Windows）/ `<venv>/bin/python`（POSIX），`args = ["-m", "evoblue_video_mcp.mcp"]`。**不使用 `uv run` 作为主形态**——多个客户端并发拉起会争 uv 锁并触发依赖同步检查，启动延迟直接叠加到每次工具调用。
- 备选形态：`evoblue-bridge` console script（`uv tool install` / `pipx` 场景）；PyInstaller exe 形态归 P7。
- 环境变量（均可选）：`EVOBLUE_ENGINE_PORT`（默认 8765）、`EVOBLUE_LOCAL_TOKEN`（开发/测试直配）、`EVOBLUE_DATA_DIRECTORY`（token 文件定位，默认系统用户数据目录）。
- token 发现顺序（只读；Bridge 永不生成或写 token 文件）：① `EVOBLUE_LOCAL_TOKEN` → ② `<data_directory>/local_token` → ③ 无 token 直连（development Engine 无鉴权）。收到 401 → `ENGINE_UNAUTHORIZED`，提示「在 WebUI 重新完成本机配对」。
- Engine 离线：返回 `ENGINE_NOT_READY`（retryable），message 指引先启动 EvoBlue Local Engine；P4 不自动拉起 Engine（ADR 0004）。

### 四客户端配置样例

Codex（`~/.codex/config.toml`，TOML 合并保留其他 `mcp_servers`）：

```toml
[mcp_servers.evoblue-video]
command = "C:\\path\\to\\.venv\\Scripts\\python.exe"
args = ["-m", "evoblue_video_mcp.mcp"]
```

Claude Desktop（`%APPDATA%\Claude\claude_desktop_config.json`）/ Claude Code（`.mcp.json` 或 `claude mcp add`），JSON 合并保留其他键：

```json
{"mcpServers": {"evoblue-video": {"command": "C:\\path\\to\\.venv\\Scripts\\python.exe", "args": ["-m", "evoblue_video_mcp.mcp"]}}}
```

DeepSeek 与 WorkBuddy：同为 STDIO `command`/`args` 形态；配置片段由 `scripts/verify_p4_clients.py` 生成，路径不确定时只输出可复制配置，不猜测。

## P4 人工验证清单（兼容矩阵）

| 客户端 | OS | 版本 | 配置模式 | 握手 | 提交 | 状态 | 报告 | 结论 |
|---|---|---|---|---|---|---|---|---|
| Codex | Windows 11 | 0.153.4 | `config.toml` `mcp_servers` | ✅ 2026-09-06（持久配置） | ✅ list_analysis_jobs | 未实测 | ✅ diagnose_environment（13 项检查） | 通过：持久配置经真实用户态 `codex mcp list` 读取，并由无 EvoBlue 临时覆盖的只读 Codex 会话完成 diagnose/list；submit/status/report 未实测 |
| Claude Code | Windows 11 | 2.1.261 | `claude mcp add`（local 作用域） | ✅ 2026-09-06 | ✅ list_analysis_jobs | ✅ | ✅ diagnose_environment | 通过（headless 真实调用留证） |
| WorkBuddy | Windows 11 | 未记录 | `~/.workbuddy/mcp.json` | ✅ 2026-09-06（从注册表真实条目启动复验） | ✅ submit 受理成功（job_id + reused=false） | ✅ 20 次轮询响应正常 | 未实测（Engine 未完成初始设置，任务按设计停在 queued；非客户端问题） | 协议链路全通：submit/status/diagnose 真机实测；报告列待 WebUI 完成 LLM 设置后由同一 job 自动开跑补测 |
| DeepSeek | — | — | 配置片段已交付 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证（本机未安装） |

## Codex

- 目标配置：`~/.codex/config.toml`（2026-09-06 实测：Codex 0.153.4 CLI 与 Desktop 共用此文件，STDIO 条目用 `command`/`args`）。
- 使用 TOML 安全解析合并指定 `mcp_servers` 项，不覆盖其他服务器或注释结构（2026-09-06 采用追加式合并 + `tomllib` 回读比对键集，其余 server 与全部注释逐键确认未变）。
- 备份后写入，验证 Bridge 启动、MCP 握手与 Engine 健康。
- 实测记录：持久配置写入后，真实用户态 Codex 0.153.4 的 `codex mcp list` / `codex mcp get evoblue-video` 均识别该条目；随后在不传入任何 EvoBlue 临时配置覆盖的 `codex exec --ephemeral --sandbox read-only` 会话中，`diagnose_environment`（13 项检查）与 `list_analysis_jobs(limit=1)` 均返回 `ok:true`。Codex 沙箱内直接运行 CLI 会切换到 `C:\Users\CodexSandboxOffline\.codex`，验证用户级配置时必须在真实用户态执行，不能把沙箱隔离误判为配置丢失。

## Claude Desktop (Windows)

- 目标配置：`%APPDATA%\Claude\claude_desktop_config.json`。
- JSON 结构化合并 `mcpServers`，保留其他键；备份、原子写入、真实握手。

## DeepSeek Harness

- 使用官方 MCP Client 插件和 STDIO Transport。
- 作为独立客户端适配器；核心包禁止依赖其内部 API。
- 格式仍变化时只生成经用户确认的配置，不猜测路径。

## WorkBuddy

- 配置位置（2026-09-06 实测）：`~/.workbuddy/mcp.json`，`mcpServers` JSON 形态，条目支持 `command`/`args`/`disabled`（另见同文件既有 `url`/`type: http`/`headers`/`env` 形态条目）；修改前备份，保留其他 server。
- 本项目实际条目 key：`evoblue-video-mcp`（`command`/`args`/`disabled:false`，无 env——开发 Engine 8765 无鉴权）。与 Codex/Claude 的 `evoblue-video` 命名不同属正常，各客户端注册表互相独立。
- 新增 server 后需在 WorkBuddy 连接器管理页「自定义连接器」手动 Trust 才激活。
- 2026-09-06 复验：直接从注册表真实条目启动（验证的就是注册配置本身）——配置读取、venv python 拉起 Bridge、stderr 日志正常、握手 `evoblue-video v0.1.0`（protocol 2025-11-25）、7 工具全部注册。
- 适配逻辑不得进入视频核心；不可靠时提供可复制配置与验证步骤。

## 兼容矩阵要求

P4/P5 对每个受支持版本记录：操作系统、客户端版本、配置模式、Schema/返回限制、默认超时、握手结果、提交/状态/报告实测结果。未知版本标记未验证，不外推兼容性。
