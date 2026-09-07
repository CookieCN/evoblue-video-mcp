# ADR 0005: P5 客户端配置写入与验证门禁

- Status: Accepted
- Date: 2026-09-07

## Context

P5 要把 P4 手工完成的客户端配置接入搬进 WebUI：安装、验证、移除、恢复。开工前
约束设计的事实：

1. **项目没有 TOML 写入库**（依赖里只有只读 `tomllib`）。Codex 的 `config.toml`
   带注释与其他 server，P4 的手工合并已验证「追加式 + 回读比对」能逐字节保住
   注释；引入 `tomlkit` 一类依赖可以结构化写，但 round-trip 会重排空白，且新增
   供应链面。
2. **WorkBuddy 的 `mcp.json` 里有我们不理解形态的条目**（`url`/`type: http`/
   `headers`/`env`），且其实际条目 key 是 `evoblue-video-mcp`（与 Codex/Claude 的
   `evoblue-video` 不同）；新增 server 后还需在 WorkBuddy UI 手动 Trust 才激活。
3. **experience #19**：读用户级 MCP 配置会把其他 server 的明文凭据带进 AI 会话；
   这些格式按设计就要求内联凭据。
4. **SYSTEM_BOUNDARIES 高危操作清单**：修改客户端配置、恢复备份必须展示影响并
   经用户确认。
5. **「UI 不虚报成功」是 PRD 验收**：写入文件 ≠ 配置成功；握手结论是「关于现在
   的事实」，不是可持久化的状态。
6. **Claude Code 的实测路径是官方 CLI**（`claude mcp add`，local 作用域）；其用户
   级存储 `~/.claude.json` 是 CLI 的内部格式，直接改写等于赌未公开格式。
7. **DeepSeek 未装机且格式仍在变化**（CLIENT_COMPATIBILITY 明确「不猜测路径」）。

## Decision

- **三档模型**（合同 §1 冻结）：`file_auto`（codex / claude_desktop / workbuddy，
  结构化合并+备份+回读验证+真实握手）、`cli`（claude_code，仅经官方 CLI，输出
  可解析才可宣称成功）、`manual`（deepseek，只渲染可复制配置）。档位按实测确定性
  划分：实测过且格式稳定的才允许自动写。
- **零依赖 TOML 外科合并**：不引入 TOML 写入库。定位 `[mcp_servers.<key>]` 区段
  （括号深度状态机，跳过引号子串，自身子表留在区段内），追加/替换/no-op 三态；
  区段外逐字节不变使「注释与其他 server 未动」由构造保证，再以 `tomllib` 对
  原文/合并文本做「删除本项目键后深相等」证明收口。
- **单一写入通道 + 双重证明 + 自动回滚**：包内所有落盘只经 `writes.commit_atomic`
  （同目录 tmp + fsync + `os.replace` + 回读 sha256 比对）；合并证明 pre-write 与
  post-commit 各跑一次（后者满足 AGENTS #7「写入后必须回读实际字节验证」）；post
  证明失败自动回滚操作前备份。结构性门禁
  `tests/contract/test_client_write_guards.py` 保证写入原语不出 `writes.py`、
  适配器写操作必经提交通道（反空转断言防模块挪动后门禁空转）。
- **条目冲突门禁**：既有条目与目标载荷只在受管字段（command/args/env）上不同 →
  静默替换（修复语义）；含非受管键 → 409 `ENTRY_MODIFIED`，`force:true` 才替换。
  替换会丢用户加的键，所以必须显式 force，且备份 + 恢复兜底。
- **握手同步 await，结论只存内存**：握手是单次二元结果，无持久进度可汇报，
  45s 封顶同步等待；结论带 `handshake_checked_at` 时间戳，Engine 重启即回
  「未验证」。不引入 202 + 轮询状态机，也不把「关于现在的事实」持久化进 SQLite。
- **token 永不写入客户端文件**：默认 env 为空；仅端口 ≠ 8765 时写
  `EVOBLUE_ENGINE_PORT`。token 由 Bridge 经数据目录自动发现，写进客户端文件只会
  增加凭据扩散面。
- **字段级提取**（experience #19 的工程化）：API 响应只含本项目条目字段、其他
  server **数量**、状态与脱敏原因；整份配置文件永不回显，stderr 尾部脱敏后截断。
- **拒绝 mkdir**：目标父目录不存在 = 客户端未安装的实测信号，创建目录等于变相
  猜路径；降级为可复制配置 + `client_directory_missing`。
- **CLI 档不碰文件、无文件备份**：`claude mcp add/get/remove` 是唯一写入面；
  `~/.claude.json` 不读不写。CLI 档的恢复路径就是幂等重装。CLI 缺失或输出不可
  解析一律「未验证」，不猜测输出含义。
- **恢复必须 confirm，且恢复本身可逆**：确认门先于一切文件系统副作用；恢复前
  对当前状态再做安全备份，返回 `restored_from` 与 `safety_backup` 两个名字。

## Consequences

- 用户的注释、其他 server、未知形态条目得到构造级 + 证明级双重保护；代价是 TOML
  合并自带一个约百行的区段状态机，靠 30+ 个边界单测钉住，客户端格式变化时改这里
  （合同与测试先行）。
- 「未验证」会成为 UI 上的常见状态（Engine 重启、首次打开页面都会看到）——这是
  诚实的代价；文案与 `handshake_reason` 把「为什么未验证」讲清楚。
- Claude Desktop 在本机未装机，P5 交付时它的 file_auto 适配器只经过合成配置的
  测试验证，真机列保持「未实测」；装机后按合同 §10 补测即可，无需改代码。
- 握手验证在 Engine 离线时也能成功（工具静态注册）——UI 必须同时展示独立的
  Engine 在线探针，否则用户会误以为端到端已通。
- 错误码表（合同 §7）成为新的冻结面，与 MCP 层 15 码无关（HTTP API 错误码，
  非工具信封），由漂移测试锁定。
