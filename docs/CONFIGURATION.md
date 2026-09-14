# Configuration Contract

## 原则

所有首次配置通过 WebUI 完成。普通用户无需编辑 JSON、TOML、YAML 或环境变量。敏感值只存系统凭据库；数据库仅保存凭据引用和脱敏状态。

## 配置域

| 域 | 关键字段 | 校验/影响 |
|---|---|---|
| LLM | provider、base URL、model、credential ref | HTTPS/明确本机例外；保存与网络验证分离（见「LLM 连接测试」）；影响配置指纹 |
| Reports | approved directory、filename template、include transcript | 路径规范化/可写；禁止越界；冲突保护 |
| ASR | enabled、model、device、compute type | 检测 FFmpeg/CTranslate2/GPU/磁盘 |
| Platform | cookie browser/profile | 不导出 Cookie，不进日志；显示风险 |
| MCP clients | detected/configured/handshake state | 结构化合并、备份、验证、恢复 |
| Storage | database path、temp policy、limits | 平台标准目录；空间预检 |
| Privacy/logging | telemetry off、retention、log level | 默认最小采集，诊断包需确认 |

## LLM 连接测试与诊断认证（F1，2026-09-10）

- **保存与网络验证分离**：保存设置不做网络探测（断网不阻塞保存）；显式
  「测试连接」与诊断 `llm_api` 才联网，二者复用同一认证规则。
- **`POST /api/settings/test`**（设置页「测试连接」按钮）：
  - 请求体 = 当前表单值（provider/base_url/model/api_key 均可选）；
  - 凭据绑定（R1b 二轮修订 2026-09-12）：凭据与其保存时的端点 origin 持久绑定
    （`app_settings.llm_credential_origin`，迁移 v10 为存量行回填当时的 base URL）；
    **修改 Base URL origin 或切换 Provider 而未提供新 Key 的保存会原子解除绑定**
    （旧凭据留在系统凭据库但不再被引用）——该边界独立于 setup_completed 生效，
    关闭 setup 后改地址同样解绑，之后的测试连接与重新开启 setup 均不得复用旧 Key。
  - GET `{base_url}/models`，Bearer 取请求体自带 Key；**未带 Key 时仅在
    Provider 一致且 base URL 规范化 origin（scheme/host/有效端口；host 不区分
    大小写、默认端口归一）与已存配置一致的前提下复用 keyring 已存凭据**——
    凭据的作用域是它被保存时的端点，不是 Provider 名字符串（评审 R1，
    2026-09-11）：改 Base URL 到新地址后留空 Key 返回 `not_configured`（提示
    为新地址显式输入 Key），绝不把旧凭据发给新站点；跨 origin 显式输入的
    Key 用于新端点。保存门禁（PUT）应用同一边界：同 Provider 改 origin 且
    未带 Key 拒绝保存（400），前端保存按钮门控同步禁用；
  - **绝不持久化**：不写数据库、不写 keyring、不改 setup_completed；
  - 不跟随重定向（Authorization 头不会到达第二个源）；不打印认证值；
  - 响应状态枚举（冻结）：`ok` / `auth_failed`(401/403) / `http_error` /
    `network_error` / `not_configured` / `keyring_error`。
- **凭据写入串行化**（评审 R2，2026-09-11）：所有 keyring 写/删经 app 级
  单线程执行器按提交顺序执行；HTTP 等待 10 s 超时只结束请求（503「尚未
  确认」），不取消也不重排操作——迟到的旧写入不可能越过并覆盖更新的成功
  保存；读操作超时仍读作「凭据库不可用」（读无顺序约束）。
- **凭据槽版本化与原子切换**（评审 R5 三轮修订，2026-09-14）：keyring 是
  按名字寻址的保险库，原地覆盖数据库仍引用的槽会让「写入成功但数据库提交
  失败」或「超时后迟到落盘的写入」把旧端点的绑定静默改指向新 Key。因此
  每次写入都使用全新槽名 `llm:{provider}:{12位随机十六进制}`，切换遵循
  **prepare → commit → cleanup**：
  1. 新 Key 先写入自己的新槽（写入未确认即 503；迟到落盘的写只留下无人
     引用的槽，其清理任务排在写入之后）；
  2. SQLite 原子提交新引用与 origin——数据库提交才是切换点，失败时旧绑定
     与旧 Key 完整无损（新槽被清理）；
  3. 提交成功后尽力清理被替换的旧槽（清理丢失只留下未被引用的陈旧秘密，
     永不产生错误绑定）。
  显式删除 Key 的顺序相反：**先在数据库解绑**（提交 ref=None），提交成功后
  才清理旧槽——数据库失败时当前绑定与其秘密保持完全可用。取消路径按
  **提交 verdict** 清理（R12 五轮修订 2026-09-14）：`on_commit` 在所有
  durable-success 路径传播取消前先置位，因此 verdict=false 的取消**证明**
  数据库未采纳——恢复指针并清理新槽；verdict=true 的取消保留新槽、清理被
  替换槽（此时删新槽反而破坏刚采纳的绑定）。存量行的旧格式引用
  `llm:{provider}` 读取时透明兼容，
  下次写入 Key 时自动迁移到版本化槽并清理旧槽。凭据库里同时存在的未引用
  槽最多是清理丢失的残留，可在系统凭据库中手动删除，不影响任何功能。
- **诊断 `llm_api`（`include_network=true`）**：同一探测，凭据按 settings 行
  的 credential ref 从系统凭据库读取（与 `ProductionHandlerFactory` 同源）；
  401/403 报「认证失败」（detail=`auth_failed`），凭据库不可读报
  `keyring_error` 且不探测；Key 本身不出现在任何输出。
- **keyring 调用有界**：设置路径上的凭据读写带 10 s 超时（线程不可取消，
  超时读作「凭据库不可用」，后续调用收敛），阻塞的 OS 凭据库不得挂起整个
  settings API。
- **模型预设**：DeepSeek 预设 `deepseek-flash`（2026-09-10 官方文档核对；
  `deepseek-chat` 已下线、`deepseek-v4-flash` 为仍接受的 legacy 名）。已存
  旧模型名的用户不静默覆盖，设置页显示可编辑迁移提示。

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

