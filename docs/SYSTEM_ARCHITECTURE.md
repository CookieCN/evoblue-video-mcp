# System Architecture

## 1. 目标

在用户电脑上可靠地把视频链接转成可搜索、可迁移、用户拥有的 Markdown。架构首先解决长任务、客户端超时、多客户端并发、进程重启和敏感凭据问题。

## 2. 部署拓扑

```text
Codex / Claude Desktop / DeepSeek Harness / WorkBuddy
                    |
              STDIO MCP Bridge
                    |
       HTTP + local access token (loopback)
                    |
      Local Engine @ 127.0.0.1:8765
       |        |         |         |
     WebUI   Settings   Job Worker  Search
                          |
                    Video Pipeline
                          |
              SQLite index + Markdown store
```

Local Engine 是唯一状态所有者。Bridge 可以有多个，Engine 和 Worker Owner 只能各有一个逻辑所有者。

## 3. 模块职责

| 模块 | 负责 | 不负责 |
|---|---|---|
| WebUI | 首次设置、任务、报告、搜索、诊断、客户端配置 | 直接执行 Pipeline |
| Local HTTP API | 鉴权、输入验证、Job/报告/设置接口 | 暴露公网接口 |
| Job Worker | 领取、持久化、重试、取消、恢复 | 仅靠内存保证执行 |
| Video Pipeline | 元数据、字幕/音频、ASR、清洗、分块、总结、报告、索引 | 客户端配置 |
| SQLite | 状态、配置元数据、FTS5 索引、幂等键 | 作为已完成报告唯一副本 |
| Markdown Store | 用户最终知识资产 | 保存密钥与 Cookie |
| STDIO Bridge | MCP 声明、Schema 校验、Engine 调用、结果转换 | 数据库/yt-dlp/FFmpeg/LLM |

## 4. Local Engine 生命周期合同

- 默认监听 `127.0.0.1:8765`；端口可配置但不得改变 loopback 默认边界。
- 安装后可由操作系统用户级自启动机制拉起，P0 只定义合同。
- Bridge 发现 Engine 离线时可发起安全启动请求；必须有单实例锁、有限重试和明确错误。
- Worker 使用数据库租约/所有权记录保证单一 Owner；租约需心跳与过期接管。
- Engine 启动时扫描 `queued`、`running`、`retry_wait`；失效的 `running` 任务转为安全恢复路径，而非永久卡住。
- 不允许把 `asyncio.create_task` 作为唯一执行保证。状态变化和阶段输入先持久化，再开始副作用。
- 停止时不接受新任务，当前阶段到安全点后持久化；强制退出后也可由幂等阶段恢复。

## 5. 数据流

1. 提交 URL，规范化后计算 `URL + mode + asr + language + config fingerprint` 幂等键。
2. 命中可复用近期 Job 时返回已有 `job_id`；否则持久化 `queued`。
3. Worker 原子领取 Job，逐阶段先写状态，再执行阶段逻辑与产物提交。
4. 报告先写同目录临时文件，校验完成后原子替换；默认不覆盖用户修改过的文件。
5. 完成后写 FTS5 索引；数据库丢失时扫描 Markdown Frontmatter 重建历史。

## 6. 配置与凭据

- 非敏感设置存 SQLite/平台标准配置目录；密钥存系统凭据库，仅保存引用。
- 配置变化生成指纹，影响任务复用而不把明文密钥写入指纹。
- WebUI 是唯一初始配置入口；CLI/环境变量只可用于开发和自动化，不成为普通用户必经路径。

## 7. 可观测性与失败边界

- 结构化日志写 stderr 或轮转文件；STDIO Bridge 的 stdout 只输出 MCP 协议。
- 每个失败提供稳定 `error_code`、脱敏 detail、是否可重试和用户可执行的下一步。
- 下载、转录、LLM、磁盘和客户端配置各自有超时、并发与重试上限。

