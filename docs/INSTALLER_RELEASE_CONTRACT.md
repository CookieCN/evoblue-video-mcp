# Installer & Release Contract (P7)

Status: frozen 2026-09-07 (P7-001)。本文是安装器、单实例、WebUI 生产化、Bridge
打包入口、迁移备份与 Release 流程的唯一权威合同；代码与脚本必须逐字对齐冻结表，
漂移测试 `tests/contract/test_installer_release_contract.py` 负责锁定。

## 0. 范围与非目标

**范围**：Windows 用户级安装/升级/卸载；便携 zip；Engine 单实例；打包形态的
WebUI（token 引导 + SPA 深链接）；打包 Bridge 入口；SQLite 迁移前备份；GitHub
Release 流程。

**非目标**：代码签名（独立成本决策，见 SUPPORT）；FFmpeg 捆绑（字幕主流程不
需要；ASR 音频路径由诊断提示 + SUPPORT 指引自装）；macOS/Linux 安装器（CI 产
便携 zip）；自动发布（Release 恒为草稿，Owner 手动发布）；远程部署；自动 git
push。

## 1. 版本与产物命名（冻结）

- 唯一版本源：`pyproject.toml` 的 PEP 440 版本（当前 `0.9.0b2`）。
  `src/evoblue_video_mcp/__init__.py` 的 `__version__` 必须与之相等；
  `frontend/package.json` 用 npm 形态（`-beta.N` ↔ PEP 440 `bN`）。禁止第三处
  字面量；派生字符串一律从 pyproject 推导。
- 版本一致性由 `tests/contract/test_version_consistency.py` 锁定。

| 产物 | 形态 | 来源 |
|---|---|---|
| `EvoBlueVideoMCP-<npmver>-setup.exe` | Inno Setup 用户级安装器 | `scripts/build_installer.py` |
| `evoblue-video-mcp-<npmver>-win-full.zip` | Windows 便携 zip（full bundle） | `scripts/build_installer.py` |
| `evoblue-video-mcp-<npmver>-<os>-full.zip` | macOS/Linux 便携 zip | release workflow |
| `SHA256SUMS.txt` | 全部产物的 SHA256 清单 | `scripts/build_installer.py` / release workflow |

`<npmver>` 是 pyproject 版本的 npm 归一化（如 `0.9.0b1` → `0.9.0-beta.1`）。
`base` 变体是打包矩阵/体积基准产物，不是面向用户的产品，不进安装器、不进 zip。

## 2. 安装布局（冻结）

| 项 | 冻结值 |
|---|---|
| AppId | `{55355472-adb9-4258-b2fe-34c2af302239}` |
| 安装根 | `{localappdata}\Programs\EvoBlue Video MCP` |
| 权限模型 | `PrivilegesRequired=lowest`，全程无管理员 |
| 安装内容 | full bundle 全树 + `THIRD_PARTY_NOTICES.md` + `ASR_MODEL_LICENSES.md` |
| 安装标记 | `<安装根>\install-version.txt`：`version=<ver>` + `data_dir=<数据目录>` 两行 |
| 开始菜单 | 「EvoBlue Video MCP」快捷方式 → 引擎 exe |
| 桌面快捷方式 | 可选任务（默认不选） |
| 自启动 | 可选任务（默认选中）：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值名 `EvoBlue Video MCP`，值 = 带引号引擎 exe 路径，`Flags: uninsdeletevalue` |
| 卸载器 | 标准 unins000.exe；卸载询问「是否同时删除个人数据」（默认保留）；**静默卸载（`/SUPPRESSMSGBOXES`）恒保留数据** |

数据目录与安装目录分离：数据目录来自 `resolve_runtime_paths`（platformdirs
`user_data_path("EvoBlue Video MCP", "EvoBlue")`）。卸载清除只删除安装时记录的
数据目录字面量，用户自定义 `report_directory` / `asr_model_dir` 永不触碰。

## 3. 单实例合同（冻结）

引擎启动时按顺序执行三步，任何一步失败即退出并给出中文文案：

1. **Mutex**（仅 Windows）：`CreateMutexW(None, FALSE, "EvoBlueVideoMCP-Engine")`，
   名称**无命名空间前缀**（per-user 会话局部；Inno `AppMutex` 在
   `PrivilegesRequired=lowest` 下自动以 `Local\` 探测，引擎侧自加前缀会造成
   `Local\Local\` 错位）。名字必须是字面量，**不带数据目录哈希后缀**——Inno
   `AppMutex` 只接受字面量名。`GetLastError() == ERROR_ALREADY_EXISTS` ⇒ 退出
   码 3。进程生命周期持有，不释放句柄。
2. **锁文件** `<data>/engine.lock`：`O_CREAT|O_EXCL` 创建，JSON
   `{"pid": int, "port": int, "started_at": float, "version": str}`。已存在时：
   解析失败或 PID 已死 ⇒ 抢占（删除重建）；PID 存活 ⇒ 退出码 3；文件 mtime 距
   今 < 5 秒且解析失败 ⇒ 拒绝抢占并退出码 3（安装/升级竞态保护）。锁抢占
   unlink+O_EXCL 非原子**是有意的**——mutex 与端口绑定才是权威，不得引入第二
   把锁。干净退出时删除锁文件；崩溃残留由下一实例按上述规则处理。
3. **端口预探测**：裸 `socket.bind(("127.0.0.1", port))` 后立即关闭。失败 ⇒
   退出码 4。端口绑定仍是最终权威（预探测与真实绑定之间的窗口由 uvicorn 兜
   底）。

| 退出码 | 含义 | 中文文案（stderr） |
|---|---|---|
| 3 | 已有 Engine 实例在运行 | `EvoBlue Engine 已在运行（端口 <port>）。请先退出当前实例，或直接使用已运行实例的 WebUI。` |
| 4 | 端口被其他程序占用 | `端口 <port> 已被其他程序占用，EvoBlue Engine 无法启动。可用环境变量 EVOBLUE_ENGINE_PORT 更换端口后重试。` |
| 0 | 启动成功 | — |

## 4. WebUI 生产化（冻结）

**frozen ⇒ production 规则**：frozen（PyInstaller）入口在用户未显式设置
`EVOBLUE_ENVIRONMENT` 时强制 `environment="production"`。判断「显式设置」看
env（frozen 子进程继承用户环境变量），不得看 Settings 默认值。

**token 引导**：production 首启生成 `local_token`（既有行为）。Engine 在
production 且 WebUI 已随包时，启动后经 `threading.Timer` 打开默认浏览器到
`http://127.0.0.1:<port>/#evoblue_token=<token>`（可用 `EVOBLUE_OPEN_UI=0` 关
闭）。fragment 携带**生效 token**——显式 `EVOBLUE_LOCAL_TOKEN` 与持久化 token
同样进入引导 URL（评审修复：env 令牌此前不进 fragment，门页指引的文件也不
存在）。**必须用 URL fragment，禁止 query**——uvicorn access log 记录完整
query string，fragment 不出网也不进日志。

| 键 | 冻结值 |
|---|---|
| URL fragment 键 | `evoblue_token`（形如 `#evoblue_token=<token>`） |
| localStorage 键 | `evoblue.local_token` |
| 401 门页文案 | 「请粘贴本机访问令牌」（指引：令牌在数据目录 `local_token` 文件中） |
| token 附加方式 | 前端 `apiFetch` 对全部 `/api/*` 请求附加 `X-Local-Token` 头 |

localStorage 按 OS 用户的浏览器配置文件隔离，因此跨 Windows 用户会话的保护不
降级：另一账户的浏览器有自己的 localStorage，拿不到本账户 token。

**SPA 深链接**：静态挂载前注册 catch-all `GET /{path:path}`——路径在 WebUI 根
目录下存在真实文件时放行（静态挂载继续服务 `/assets/*`），否则返回
`index.html`（React Router 接管 `/settings`、`/mcp`、`/models`）。

## 5. 打包 Bridge 入口（冻结）

- 引擎 exe 接受 `bridge` 子命令：`<引擎exe> bridge` 等价于
  `python -m evoblue_video_mcp.mcp`（stdio，stdout 纯净不变）。
- frozen 客户端配置载荷：`command = <引擎exe路径>`，`args = ["bridge"]`，env
  仅在端口 ≠ 8765 时含 `EVOBLUE_ENGINE_PORT`；永不写 token（token 经数据目录
  `local_token` 文件自动发现）。源码形态载荷不变
  （`<python>` + `["-m", "evoblue_video_mcp.mcp"]`）。
- Bridge 永不自动拉起 Engine（ADR 0004 维持）；Engine 离线 ⇒
  `ENGINE_NOT_READY` + 启动指引。

## 6. 升级与数据（冻结）

- **迁移前备份**：Engine 在 `init_db` 应用任何版本跳变（`0 < 当前版本 < 目标`
  ）前，用 SQLite backup API（非文件复制——WAL 下裸复制可能拿到陈旧快照）把
  数据库快照为 `<数据目录>/evoblue.db.bak-v<当前版本>`（如 `evoblue.db.bak-v7`
  ）。备份失败记 `MIGRATION_BACKUP_FAILED` 并**继续迁移**（fail-open：Markdown
  是最终恢复资产，见 MIGRATION_ROLLBACK 规则 3）。备份保留全部历史（不剪枝），
  手动清理。
- **自启动语义**：登录即启动引擎；安装时默认选中，卸载随 `uninsdeletevalue`
  移除。
- **卸载语义**：默认保留全部用户数据（数据库、报告、模型、token）；用户在卸
  载确认框选择后才删除数据目录。升级 = 原地重装（同 AppId），用户数据天然保
  留。

## 7. Release 流程（冻结）

1. Owner 本地：确认 `docs/RELEASE_VERIFICATION.md` 清单后 bump 版本、commit、
   push、打 tag `v<pyproject版本>`（如 `v0.9.0-beta.1`）并 push tag。
2. release workflow（`on.push.tags: ["v*"]`，`contents: write`）：先校验
   tag == pyproject 版本（失败快速退出），再矩阵构建 + 验证 + 打包产物。
3. Windows job 额外跑 `verify_release.py` 与 setup.exe 静默安装/卸载冒烟。
4. 全部 job 成功后创建**草稿** Release（`--draft --generate-notes`）并附全部
   产物与 `SHA256SUMS.txt`；同 tag 已有草稿则失败（防重复）。
5. Owner 审阅草稿、回填实测体积/RTF 数字后手动发布。

## 8. 验收矩阵（P7 关门）

| 场景 | 验证方式 |
|---|---|
| 干净安装 → WebUI 首次设置 | `scripts/verify_p7_acceptance.py`（静默装 + 生产启动 + health）+ 本机真实浏览器流 |
| 双开友好退出 | 集成测试（退出码 3 + stderr 文案） |
| 端口占用友好退出 | 集成测试（退出码 4 + stderr 文案） |
| frozen Bridge 握手 | `verify_release.py` 步骤 4（真实 bundle exe bridge 子命令 MCP 握手） |
| 升级备份 | 集成测试（v7→v8 生成 bak-v7）+ 验收脚本原地重装 |
| 卸载保数据 | 验收脚本 + release workflow 冒烟；数据清除路径人工清单 |
| 版本一致性 | `test_version_consistency.py`（三处来源 + npm 归一化） |
| 合同漂移 | `test_installer_release_contract.py` |
