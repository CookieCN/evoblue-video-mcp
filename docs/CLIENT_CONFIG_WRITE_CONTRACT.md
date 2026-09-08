# Client Config Write Contract（P5 冻结）

状态：已冻结（P5 合同 1，日期 2026-09-07）。本文档是 WebUI 对 MCP 客户端配置执行
「安装 / 验证 / 移除 / 恢复」的唯一权威合同。实现与测试以本文档为准，漂移由
`tests/contract/test_client_write_contract.py` 锁定，写入路径由
`tests/contract/test_client_write_guards.py` 结构性锁定。客户端事实（实测路径、
格式、版本矩阵）以 `docs/CLIENT_COMPATIBILITY.md` 为权威，本文档不重复实测记录。

## 0. 范围与非目标

范围内：对 §1 注册表中的每个客户端执行 `install` / `verify` / `remove` / `restore`，
并随时渲染可复制配置（render-copyable）。所有自动化结果只回答两类问题：配置是否
真实写入并通过回读验证、Bridge 是否真实握手成功；无法确认时一律返回「未验证」。

非目标（冻结清单，违反任何一条即为缺陷）：

- Engine 一键拉起（归 P7，ADR 0004 / ADR 0005）。
- 猜测未知路径：目标父目录不存在时不得创建目录、不得尝试替代路径。
- 覆盖其他 MCP 服务器配置：合并只允许触碰本项目的条目键。
- DeepSeek 自动写入。
- 读取或回显整份用户配置文件（字段级提取，见 §8）。

## 1. 客户端注册表（冻结）

| client_id | 显示名 | tier | 条目 key | 容器 | 写入目标 | 备注 |
|---|---|---|---|---|---|---|
| codex | Codex | file_auto | evoblue-video | toml | ~/.codex/config.toml | |
| claude_desktop | Claude Desktop | file_auto | evoblue-video | json | %APPDATA%/Claude/claude_desktop_config.json | 仅 Windows 实测路径，POSIX 不支持自动写 |
| workbuddy | WorkBuddy | file_auto | evoblue-video-mcp | json | ~/.workbuddy/mcp.json | 安装后需在 WorkBuddy 连接器管理页手动 Trust 才激活 |
| claude_code | Claude Code | cli | evoblue-video | cli | claude mcp（local 作用域） | CLI 不在 PATH 时该次调用降级为可复制配置，绝不直接改写 CLI 管理的文件 |
| deepseek | DeepSeek Harness | manual | evoblue-video | json | 无 | 格式仍在变化，只生成可复制配置，永不自动写入 |

tier 语义：`file_auto` = 结构化合并 + 备份 + 回读验证 + 真实握手；`cli` = 经客户端
官方 CLI 增删条目，输出可解析才可宣称成功；`manual` = 仅渲染可复制配置。

## 2. 路径解析

- 解析只依赖两类注入来源：用户主目录（生产环境 `Path.home()`，测试注入临时目录）
  与 `%APPDATA%` 环境变量；除此之外不得引入任何路径来源（不扫描安装目录、不查注册表、
  不探测可执行文件位置）。
- `~` 前缀按主目录展开；`%APPDATA%` 按 `APPDATA` 环境变量展开，缺失即视为该客户端
  不支持自动写（`claude_desktop` 在非 Windows 环境的表现）。
- **父目录必须存在**：目标文件的父目录不存在 → 视为客户端未安装，不创建目录、
  不写入，返回 `performed:false` + 原因 `client_directory_missing` + 可复制配置。

## 3. 条目载荷与 env 策略

- `command` = Engine 进程自身的解释器（生产环境 `sys.executable`）；`args` 固定为
  `["-m", "evoblue_video_mcp.mcp"]`（CLIENT_COMPATIBILITY「Bridge 启动合同」）。
- 打包形态分支（P7，INSTALLER_RELEASE_CONTRACT §5）：frozen（PyInstaller）进程
  的载荷为 `command = <引擎exe路径>`、`args = ["bridge"]`（引擎 exe 的 `bridge`
  子命令）；两形态按 `sys.frozen` 互斥，env 策略与 token 禁写规则不变。
- 受管字段（managed keys）= `command` / `args` / `env`。合并与冲突判定只看这三个键。
- env 策略：仅当 Engine 端口 ≠ 8765 时写入 `EVOBLUE_ENGINE_PORT=<port>`；**永不把
  token 写入任何客户端配置文件**（Bridge 经数据目录 `local_token` 文件自动发现，
  凭据扩散最小化）。
- JSON 客户端条目不写 `disabled` 字段（缺省即启用）；条目比较时显式
  `disabled:false` 视为与不写字段等价（2026-09-07 真机实测 WorkBuddy 条目形态），
  其原字节原样保留；`disabled:true` 是用户的有意禁用，视为非受管差异
  （`ENTRY_MODIFIED`）。
- 条目 key 按 §1 注册表冻结：workbuddy 用 `evoblue-video-mcp`，其余用 `evoblue-video`；
  各客户端注册表互相独立，不做跨客户端统一或迁移。

## 4. 合并语义

### 4.1 TOML（codex）

零依赖外科合并（项目无 TOML 写入库，只有只读 `tomllib`；延续 P4 实测路线）：

- **行扫描**：逐行统计方括号深度，跳过引号子串（`"..."` 含反斜杠转义、`'...'`），
  `[[` / `]]` 原子消费；头行 = 行首与行末深度均为 0 且形如 `[table.path]` 或
  `[[array.of.tables]]`（允许行内注释）。多行数组内的 `[`、多行字符串（`"""`）内的
  伪头行不得误判。
- **定位**：第一个 path 为 `mcp_servers.<条目 key>` 的头行（裸键与引号键
  `"evoblue-video"` / `'evoblue-video'` 归一化后等价）。区段 = 该行起，至下一个
  不属于自身子树的头行止——`[mcp_servers.<key>.env]` 等自身子表留在区段内；
  `[mcp_servers.<其他>]`、`[其他]`、`[[...]]` 终止区段；无下一个头行则到 EOF。
- **fail closed**：`tomllib` 解析原文失败（语法错误、重复表），或条目键存在于解析
  结果但找不到对应头行（dotted-key 写法）→ `CONFIG_UNSUPPORTED`，不写入。
- **合并决策**（权威是 `tomllib` 解析结果，不是行扫描）：键缺失 → 文件末尾追加
  （不足尾换行先补，再空一行）；条目与目标载荷相等 → no-op（不落盘）；差异仅在
  受管字段 → 替换区段；差异含非受管键 → `ENTRY_MODIFIED` 冲突，需 `force:true`
  才替换（备份 + 恢复兜底）。
- **编码**：BOM 保留（读时剥离、写回补回）；换行风格取全文主导（CRLF/LF 计数，
  平手取 LF），仅新增行使用主导风格，保留行字节原样——输出 = 区段前原文 + 新块 +
  区段后原文，区段外逐字节不变（注释保留由构造保证）。
- **证明（pre + post 双跑）**：写入前对合并文本、提交回读后对真实字节各跑一次
  `prove_merge`：① 合并文本可被 `tomllib` 解析；② `mcp_servers.<key>` == 目标载荷；
  ③ 对原文与合并文本各删掉 `mcp_servers.<key>` 后深相等（其余一切未动）。

### 4.2 JSON（claude_desktop / workbuddy / deepseek 样例）

- BOM → `CONFIG_UNSUPPORTED`（客户端的 `JSON.parse` 同样拒绝 BOM 文件）。
- 重复键（任意层级）→ `CONFIG_UNSUPPORTED`（JSON last-wins 语义会静默改写用户数据）。
- 根必须为对象；`mcpServers` 缺失 → 追加新建；存在但非对象 → `CONFIG_UNSUPPORTED`。
- 只赋值本项目的条目键一个键；同文件中其他条目（`url` / `type:http` / `headers` /
  `env` 等未知形态）原样存活。
- 条目相等 → no-op（不落盘）；受管字段差 → **原位替换**（键位置保持）；含非受管键
  → `ENTRY_MODIFIED`（同 4.1 的 force 规则）。
- 序列化：`json.dumps(indent=2, ensure_ascii=False)` + 尾换行（中文路径不转义；
  JSON 无注释，空白归一化是已记录的已知代价）。证明额外断言删除本项目键后的
  顶层与 `mcpServers` 子键序列一致（键序保持）。

### 4.3 幂等与冲突

- 对已处于目标状态的配置重复执行 install：不产生任何磁盘写入（no-op），仍执行
  真实握手并返回最新验证状态。
- 并发：同一客户端的变更操作被逐客户端串行化（`asyncio.Lock`）；锁被占用 →
  `OPERATION_IN_PROGRESS`。

## 5. 备份与恢复

冻结常量：

| 常量 | 值 |
|---|---|
| BACKUP_SUFFIX | .evoblue-backup- |
| BACKUP_RETENTION | 5 |
| ABSENT_SENTINEL | EVOBLUE_ABSENT_SENTINEL |

- 命名：`<完整文件名><BACKUP_SUFFIX><UTC yyyyMMddTHHMMSS>Z-<4位hex>`，
  与目标同目录（同目录保证 `os.replace` 原子性）；同秒冲突由 4 位 hex 后缀区分。
- `create_backup`：读当前字节（目标不存在 → 哨兵行文件）→ 经唯一写入通道原子落盘 →
  修剪该目标的历史备份至最新 `BACKUP_RETENTION` 份（修剪失败仅降级告警，不失败）。
- **恢复（高危操作，必须显式 confirm）**：确认门在进入任何文件系统操作**之前**
  校验（422 先于一切副作用）；备份名必须属于该目标（否则 404）；流程 = ① **先把
  选中备份读入内存**（安全备份触发保留修剪，满架时最旧备份——可能正是用户选中的
  恢复点——会被剪掉，2026-09-07 评审修复）→ ② 对当前状态再做一份安全备份（恢复
  可逆）→ ③ 哨兵备份则删除目标文件，否则原子写回备份字节 → ④ 对恢复后字节跑解析
  证明 → 返回 `restored_from` 与 `safety_backup` 两个备份名。目标与备份内容不一致
  不是错误：确认前用 `matches_current` 提示「恢复将覆盖此后的修改」，确认后由安全
  备份兜底。
- `cli` 档无文件备份（CLI 管理的文件不得触碰）；其恢复路径就是重新执行 install
  （同一载荷，幂等）。

## 6. 验证门禁与状态字符串（冻结）

握手成功判据：以目标载荷原样拉起 Bridge（env = 进程环境 ∪ 条目 env），MCP
`initialize` + `list_tools` 全部成功，且 `server_info.name == "evoblue-video"`、
工具名集合与 `TOOL_NAMES` 完全一致。总超时 45s（内核内 `asyncio.wait_for` 封顶）。

状态枚举与展示标签（冻结，漂移锁定）：

| 状态 | 展示标签 | 语义 |
|---|---|---|
| verified | 已验证 | 写入经回读证明成立，且真实握手成功 |
| unverified | 未验证 | 无法确认：未执行握手、配置解析失败、CLI 缺失或输出不可解析、Engine 离线等 |
| failed | 验证失败 | 真实握手已执行且失败（含超时、名称/工具集不匹配） |

「未验证」规则：任何未跑真实握手、目标不可解析、CLI 不可用/输出不可解析的情形，
一律 `unverified` 并携带机器可读原因（`handshake_reason`）；UI 不得在此状态下显示
成功。写入文件成功本身**永远不构成**成功状态。

门禁链（按操作）：

- install（file_auto）：路径解析（父目录缺失 → 可复制配置，不写）→ 读 → 解析 →
  合并决策 → 冲突门 → 备份 → 原子提交 → 回读字节比对 → pre 证明 → post 证明 →
  真实握手 → 状态。post 证明失败 → 自动回滚操作前备份 → `CONFIG_WRITE_FAILED`
  （消息说明回滚是否成功）。
- verify：纯只读（绝不写文件）：解析 → 条目在且与目标载荷一致 → 真实握手。
- remove（file_auto）：confirm → 备份 → 原文减去自身区段（含自身子表）→ 原子提交 →
  回读 → 证明（条目缺失且其余不变）。条目本就缺失 → 幂等成功。
- restore：§5 状态机 + 恢复后解析证明。
- cli 档：install / remove 经官方 CLI；仅当 `claude mcp get` 退出码 0 且输出可
  解析出 command、args、scope 三者与目标载荷**全部等价**（Windows 路径大小写不
  敏感）才 `verified`；任何字段无法确认（含 env——get 输出不回显 env，非空 env
  的条目一律不可验证）、CLI 缺失、输出不可解析、超时一律 `unverified` + 对应
  原因（2026-09-07 评审修复：只匹配 command 会让错误模块/作用域也显示已验证）。
- manual 档：一切变更操作拒绝（409）；永远提供可复制配置。

状态可见性：握手结论仅存内存并携带 `handshake_checked_at` 时间戳，Engine 重启后
回到 `unverified`；**配置证据消失即结论失效**——verify 发现条目不匹配/缺失/不可
解析、或状态查询发现已验证条目不再与目标载荷一致时，缓存的 verified 立即失效为
`unverified/config_changed`（2026-09-07 评审修复：不能同时报告「已验证」与「条目
不匹配」）。握手成功不代表 Engine 在线（工具静态注册，离线也可 list_tools），
Engine 在线由独立探针呈现。manual 档没有任何客户端配置证据，其 verify 永远是
`unverified/manual_tier`——载荷握手只证明 Bridge 能启动，不证明客户端已配置。

## 7. HTTP API

- 路由前缀 `/api/mcp-clients`，加入 P3 结构化信封前缀表（错误 =
  `{"error": {code, message}}`）；服务未注入时端点整体不注册（裸应用 404）。
- 握手验证为同步 await（单一二元结果、无持久进度状态，45s 封顶）；不用 202 + 轮询
  （对照：模型下载有持久进度才用 202）。
- 变更操作逐客户端串行化；阻塞文件系统操作经 `asyncio.to_thread`。

| 方法 | 路径 | 请求体 | 成功响应 |
|---|---|---|---|
| GET | /api/mcp-clients | — | 客户端状态列表 |
| POST | /api/mcp-clients/{client_id}/install | {force?: bool} | 操作结果 |
| POST | /api/mcp-clients/{client_id}/verify | — | 操作结果 |
| POST | /api/mcp-clients/{client_id}/remove | {confirm: bool} | 操作结果 |
| GET | /api/mcp-clients/{client_id}/backups | — | 备份列表 |
| POST | /api/mcp-clients/{client_id}/restore | {backup_name: str, confirm: bool} | 操作结果 |
| GET | /api/mcp-clients/{client_id}/config | — | 可复制配置 |

错误码（冻结，漂移锁定；顺序即下表）：

| HTTP | code | 触发条件 |
|---|---|---|
| 404 | CLIENT_UNKNOWN | client_id 不在注册表 |
| 404 | BACKUP_NOT_FOUND | restore 指定未知或不属于该目标的备份名 |
| 409 | ENTRY_MODIFIED | 既有条目含非受管键且未指定 force |
| 409 | OPERATION_IN_PROGRESS | 同一客户端已有变更操作在执行 |
| 409 | AUTO_INSTALL_UNSUPPORTED | 对 manual 档发起变更操作 |
| 422 | CONFIRMATION_REQUIRED | remove / restore 未携带 confirm:true |
| 422 | INVALID_REQUEST | P5 请求体校验失败（缺 body、字段类型错误、必填字段缺失） |
| 503 | CONFIG_WRITE_FAILED | 目标不可写（权限/占用）、提交后证明失败（含回滚结果说明） |
| 503 | CONFIG_UNSUPPORTED | 目标存在但不受支持（解析失败、BOM JSON、重复键、dotted-key TOML 条目） |

## 8. 字段级提取与脱敏（冻结）

- API 响应只包含：本项目条目的字段（command / args / enabled 类状态）、其他
  服务器的**数量**（`other_server_count`，不含名字、结构与内容）、状态标志与
  脱敏后的原因文本。绝不回显整份配置文件、其他条目的键名或内容。
- 绝对路径只允许出现在可复制配置的 `target_path` 字段（用户本就需要它）；
  错误消息中的路径经 `redact_path` 收缩为尾部片段。
- Bridge stderr 尾部（排障用）经脱敏（绝对路径收缩、token/password/api_key 模式
  替换）后 ≤ 2000 字符。

## 9. 渲染样例（字节锁定）

以下样例与 `CLIENT_COMPATIBILITY.md`「四客户端配置样例」对应条目逐字节一致
（样例 command 为文档占位路径，运行时以真实解释器路径渲染）。

Codex（TOML）：

```toml
[mcp_servers.evoblue-video]
command = "C:\\path\\to\\.venv\\Scripts\\python.exe"
args = ["-m", "evoblue_video_mcp.mcp"]
```

Claude Desktop（JSON，单行形态）：

```json
{"mcpServers": {"evoblue-video": {"command": "C:\\path\\to\\.venv\\Scripts\\python.exe", "args": ["-m", "evoblue_video_mcp.mcp"]}}}
```

WorkBuddy（JSON，缩进形态，条目 key 不同）：

```json
{
  "mcpServers": {
    "evoblue-video-mcp": {
      "command": "C:\\path\\to\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "evoblue_video_mcp.mcp"
      ]
    }
  }
}
```

DeepSeek 使用 Claude Desktop 形态（key `evoblue-video`，仅作为可复制配置渲染，
永不自动写入）。`claude_code` 的可复制配置为 CLI 命令文本（`claude mcp add`，
local 作用域，`--` 后接 command 与 args），由运行时按真实路径与引号规则生成，
不在此字节锁定。

## 10. 实测记录

按 CLIENT_COMPATIBILITY「兼容矩阵要求」填写；未实测项保持「未实测」，不外推。
2026-09-07 真机验收（Windows 11，验收驱动 `scripts/verify_p5_acceptance.py`，
12/12 通过；握手目标 = 127.0.0.1:8765 真实 Engine；HTTP 层经 18888 端口隔离
实例冒烟）：

| 客户端 | OS | 版本 | P5 实测操作 | 写入/回读证明 | 握手 | 恢复 | 日期 | 结论 |
|---|---|---|---|---|---|---|---|---|
| codex | Windows 11 | 0.153.4 | install/verify/remove/restore 全循环 + HTTP install | TOML 外科合并；P4 时代 `f:\` 条目按受管修复规整为 `F:\`（备份 cb53/268d/f68b 留证） | ✅ 真实握手 ×3 | ✅ remove→restore 往返 + 安全备份 | 2026-09-07 | 通过（验收后保留规范化条目；WebUI 重复安装为真 no-op） |
| claude_desktop | Windows 11 | 未记录（%APPDATA%\Claude 残留自 2025-01） | install（真实写入）→ restore 前状态（自清理） | JSON 合并写入新条目并回读证明；验收后还原 23 字节空注册表 | ✅ 真实握手 | ✅ 哨兵无关的备份恢复 | 2026-09-07 | 通过（父目录存在启发式对残留目录同样生效；写入与恢复均有证明） |
| workbuddy | Windows 11 | 未记录 | install + verify | 实测条目含 `disabled:false` 与小写盘符：`disabled:false` 归一化为 no-op（合同 §3）；盘符大小写差异触发受管修复 | ✅ 真实握手 | 未实测（不走 remove，避免 Trust 状态风险） | 2026-09-07 | 通过（验收后用户文件还原为验收前字节；注意：WebUI 安装会规整大小写并去除冗余 `disabled:false`，可能触发 WorkBuddy 重新 Trust） |
| claude_code | Windows 11 | 2.1.261 | verify（CLI get + 真实握手） | CLI 旗标实测：`add --scope` ✅、`remove -s` ✅、`get` **无 scope 旗标**；输出中路径大小写不敏感比对 | ✅ 真实握手 | 未实测（CLI 档恢复=重装，见 §5） | 2026-09-07 | 通过（真机发现 `get --scope` 无效旗标并修复适配器） |
| deepseek | — | — | 变更操作拒绝验证 | 无（manual 档永不写入） | — | — | 2026-09-07 | 未实测（本机未安装；`AUTO_INSTALL_UNSUPPORTED` 门禁实测通过） |
