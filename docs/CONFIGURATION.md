# Configuration Contract

## 原则

所有首次配置通过 WebUI 完成。普通用户无需编辑 JSON、TOML、YAML 或环境变量。敏感值只存系统凭据库；数据库仅保存凭据引用和脱敏状态。

## 配置域

| 域 | 关键字段 | 校验/影响 |
|---|---|---|
| LLM | provider、base URL、model、credential ref | HTTPS/明确本机例外；测试连通性；影响配置指纹 |
| Reports | approved directory、filename template、include transcript | 路径规范化/可写；禁止越界；冲突保护 |
| ASR | enabled、model、device、compute type | 检测 FFmpeg/CTranslate2/GPU/磁盘 |
| Platform | cookie browser/profile | 不导出 Cookie，不进日志；显示风险 |
| MCP clients | detected/configured/handshake state | 结构化合并、备份、验证、恢复 |
| Storage | database path、temp policy、limits | 平台标准目录；空间预检 |
| Privacy/logging | telemetry off、retention、log level | 默认最小采集，诊断包需确认 |

## 本机访问令牌（P7）

打包形态（frozen ⇒ production，`INSTALLER_RELEASE_CONTRACT` §4）：

- 首次启动在数据目录生成 `local_token`（随机），全部 `/api/*` 数据端点要求
  `X-Local-Token` 头（`/api/health` 豁免）；
- Engine 启动后自动打开浏览器到 `/#evoblue_token=<token>`（fragment 不进
  uvicorn 访问日志；`EVOBLUE_OPEN_UI=0` 关闭）；WebUI 把 token 存入浏览器
  localStorage（按 OS 用户隔离）后立即从地址栏抹除；
- 浏览器丢失 token（清存储/换浏览器）时，WebUI 显示粘贴门页——打开数据目录
  的 `local_token` 文件复制即可；
- Bridge/客户端不读该配置：token 经数据目录文件自动发现（ADR 0004）。

## 默认值

- Engine：`127.0.0.1:8765`。
- 遥测：关闭。
- 报告：UTF-8 标准 Markdown，不默认覆盖修改文件，不默认返回/展示超长完整字幕。
- ASR：`auto` 能力检测，但未经用户下载模型或同意不得隐式下载大文件。

## 配置指纹

Job 复用指纹包含会影响结果的模型、Prompt/Schema、语言、ASR 和报告版本；密钥明文、绝对用户路径与日志设置不得进入。指纹算法和版本必须持久化，可解释为何复用/不复用。

## 开发配置

环境变量可用于测试和开发，但不得成为发行版唯一配置途径。P0 的 `Settings` 只提供安全默认值，不读取真实凭据。

