# Client Compatibility Contract

## 共同行为

- STDIO Transport；Bridge stdout 只承载 MCP 协议，日志写 stderr/文件。
- `submit_video_analysis` 应快速返回，客户端无需保持一次长调用。
- 配置修改前备份、结构化合并、保留其他服务器，之后进行真实握手。
- 自动化不能确认成功时必须返回“未验证”，不得仅因写入文件就显示成功。

## Codex

- 目标配置：`~/.codex/config.toml`。
- 使用 TOML AST/安全解析合并指定 `mcp_servers` 项，不覆盖其他服务器或注释结构。
- 备份后写入，验证 Bridge 启动、MCP 握手与 Engine 健康。

## Claude Desktop (Windows)

- 目标配置：`%APPDATA%\Claude\claude_desktop_config.json`。
- JSON 结构化合并 `mcpServers`，保留其他键；备份、原子写入、真实握手。

## DeepSeek Harness

- 使用官方 MCP Client 插件和 STDIO Transport。
- 作为独立客户端适配器；核心包禁止依赖其内部 API。
- 格式仍变化时只生成经用户确认的配置，不猜测路径。

## WorkBuddy

- 输出/安装本地 MCP 配置并提供独立 Skill。
- 适配逻辑不得进入视频核心；不可靠时提供可复制配置与验证步骤。

## 兼容矩阵要求

P4/P5 对每个受支持版本记录：操作系统、客户端版本、配置模式、Schema/返回限制、默认超时、握手结果、提交/状态/报告实测结果。未知版本标记未验证，不外推兼容性。

