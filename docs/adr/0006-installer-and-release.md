# ADR 0006: 安装器、单实例与 WebUI 生产化

- Status: Accepted
- Date: 2026-09-07

## Context

P0–P6 交付的是「开发者能跑」的产品：PyInstaller onedir 双变体（ASR-4）验证了
打包链路，但发行 bundle 从未被当作产品对待。开工 P7 前核实的事实：

1. **打包引擎实际裸奔**：bundle 内无任何环节设置 `EVOBLUE_ENVIRONMENT`，
   `Settings.environment` 默认 `development` ⇒ 发行 exe 无 token 生成、无鉴权。
   `_persisted_local_token` 只在 production 分支触发。这不是理论风险：loopback
   对整台机器开放，同机其他 Windows 账户可无凭据读写全部数据端点。
2. **打包 WebUI 深链接 404**：`StaticFiles(html=True)` 只服务 `/`；
   `/settings`、`/mcp`、`/models` 在打包形态直接 404（开发时靠 Vite SPA
   fallback）。浏览器流从未进入验收。
3. **frozen 客户端配置载荷错误**：`default_payload` 用 `sys.executable` +
   `-m`，frozen 后等于让 MCP 客户端去拉起引擎 exe 本身（而非 Bridge）；合成进
   客户端配置文件即坏。合同 L14 本来就把「PyInstaller exe 形态」预留给 P7。
4. **uvicorn access log 记录完整 query string**（h11_impl.py 实测源码），token
   走 query 会进本地日志；fragment 不会。
5. **PyInstaller 双 EXE 陷阱**：共享 Analysis 的两个 EXE，bootloader 会按 TOC
   顺序把两个 PYSOURCE 都执行（引擎绑定端口后 Bridge 又启动）。做对需要 per-EXE
   TOC 过滤 + 共享 PYZ 联合，版本敏感且只有真实构建能验证。
6. **Inno AppMutex 只接受字面量 mutex 名**，且 `PrivilegesRequired=lowest` 下
   自动以 `Local\` 前缀探测——引擎侧 mutex 必须是无命名空间前缀的字面量名。
7. **未签名 Windows 程序的 SmartScreen/杀软误报**是 PRD 列明的 P7 风险；
   setup.exe 与 zip 双通道互为兜底，签名证书是独立成本决策。

## Decision

- **frozen ⇒ production**（合同 §4）：frozen 入口在未显式设
  `EVOBLUE_ENVIRONMENT` 时强制 production。实现放 `__main__.main()` 的
  `model_copy`，不放 `Settings` 默认值——否则全套测试的 Settings 行为改变。
- **token 引导走 fragment + localStorage**（合同 §4）：引擎开机自动开浏览器到
  `#evoblue_token=<token>`；前端读 fragment → localStorage → `replaceState`
  抹除 → `apiFetch` 统一附 `X-Local-Token`；401 渲染粘贴门页。localStorage
  按浏览器配置文件（= OS 用户）隔离，跨账户保护不降级。
- **SPA catch-all**（合同 §4）：静态挂载前注册 `GET /{path:path}`，真实文件
  放行、其余回 `index.html`。
- **Bridge 用子命令，不造第二个 EXE**（合同 §5）：引擎 exe 的 `bridge` 子命令
  委托 `mcp.__main__.main`。源码形态 `python -m evoblue_video_mcp bridge` 可
  测，打包形态零 spec 手术；代价只是客户端配置里 command 显示为引擎 exe 路径
  （准确、可接受）。`default_payload` 按 `sys.frozen` 分支。
- **单实例 = mutex + 锁文件 + 端口预探测**（合同 §3）：mutex 是无前缀字面量
  名（AppMutex 兼容）；锁文件承载 pid/port/started_at/version，抢占规则含安装
  竞态保护；端口预探测把 `WSAEADDRINUSE` 从栈回滚翻译成退出码 4 + 中文文案。
  锁抢占非原子是明示的设计让步——mutex 与端口绑定才是权威。
- **迁移备份用 SQLite backup API**（合同 §6）：WAL 下 `shutil.copy2` 单文件可
  能快照不一致；backup API 在只读连接上取一致快照，fail-open（Markdown 是最
  终恢复资产）。
- **Inno Setup per-user 安装器 + 便携 zip 双通道**（合同 §1/§2）：未签名安装
  器是杀软误报高发形态，zip 是被拦截用户的第二路径；`base` 变体保持为打包矩
  阵产物，不进安装器。
- **Release 恒为草稿**（合同 §7）：tag 触发 CI 矩阵构建与验证，产物进草稿
  Release，Owner 审阅后手动发布——发布是商业动作，不是 CI 动作。

## Consequences

- 发行形态首次具备与源码形态对等的安全默认（token、production 鉴权）与完整
  WebUI（深链接 + token 引导）；`verify_release.py` 步骤 1/3 的裸调用随之改为
  token-aware，步骤 4 新增 frozen bridge 真实握手。
- 客户端配置载荷出现第二种合法形态（frozen），`CLIENT_CONFIG_WRITE_CONTRACT`
  §3 与 `CLIENT_COMPATIBILITY.md` 启动合同同步修订，漂移测试锁定两形态互斥。
- 每次发版 bump 版本时只有两处 PEP 440 字面量 + 一处 npm 字面量，且
  `test_version_consistency.py` 强制一致；安装器与 release workflow 从 pyproject
  派生显示版本。
- 单实例、端口占用、token 门页成为 P8 加固矩阵的前两行：失败模式从此有稳定
  退出码与中文文案，而不是 Python 栈回滚。
