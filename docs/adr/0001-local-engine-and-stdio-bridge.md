# ADR 0001: Local Engine and STDIO Bridge

- Status: Accepted
- Date: 2026-08-24

## Context

MCP 客户端可能约 60 秒超时、会被关闭，且多个客户端可能同时提交同一视频。视频抓取、ASR 和 LLM 总结远长于一次可靠工具调用。如果每个 Bridge 自己执行任务，会产生重复下载、状态分裂和客户端退出即中断。

## Decision

采用单机模块化架构：一个仅监听 `127.0.0.1` 的持久 Local Engine 持有 SQLite、Worker、Pipeline、WebUI 和报告；每个客户端启动轻量 STDIO MCP Bridge，Bridge 只调用 Engine HTTP API。提交工具立即返回 `job_id`，客户端通过状态与报告工具继续读取。

## Consequences

- 用户关闭 AI 客户端后任务仍可继续，多个客户端共享历史并复用同一 Job。
- Bridge 容易适配不同客户端，核心业务不依赖 Codex/Claude/DeepSeek/WorkBuddy 配置格式。
- 需要解决本机 Engine 单实例、访问 Token、生命周期、版本握手和恢复，这些由 P1/P4 实现并测试。
- P0 不实现守护进程，只固定边界与协议合同。

## Rejected alternatives

- **Bridge 内同步执行**：客户端超时和关闭会丢任务。
- **每客户端一个 Engine**：重复工作、数据分裂、冲突写报告。
- **远程 SaaS**：违反 local-first、隐私与首版范围。

