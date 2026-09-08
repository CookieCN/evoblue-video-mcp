# Release Verification Checklist (ASR-4)

发布前人工执行。自动化部分已由 `scripts/verify_release.py` 覆盖；本清单是完整
验收单，包含自动化无法在本机覆盖的网络与环境项。任何一项 FAIL 都阻断发布。

## 1. 自动化验证（每台构建机）

```bash
uv run python scripts/build_package.py            # base + full 双变体
uv run python scripts/verify_release.py dist/evoblue-video-mcp-full
```

已覆盖：干净数据目录启动、/api/health、/api/models 审批标志、production
token 文件生成与 401/200 行为、损坏模型下 Engine 存活 + 稳定错误码。

## 2. 打包矩阵与体积增量

- [ ] Windows：base 与 full 都构建成功（本机已验：base 47.4 MB / full 120.9 MB / ASR 运行时增量 73.5 MB）
- [ ] macOS / Linux：CI `package` workflow 产物可下载且体积记录在案
- [ ] base 变体内无 `sherpa_onnx` / `onnxruntime` / `numpy`（`dist/evoblue-video-mcp-base/_internal` 无对应目录）
- [ ] 两个变体都无模型权重（全盘搜索 `*.onnx` 只允许 Silero VAD 资产）
- [ ] `THIRD_PARTY_NOTICES.md` 与 `docs/ASR_MODEL_LICENSES.md` 已在 bundle 根目录

## 3. CPU 兼容

- [ ] 门禁机器实测 RTF 记录在 `benchmarks/results/`（当前机器：Standard 0.06 / Lite 0.03）
- [ ] full 变体在有真实模型时 Provider 注册成功（启动日志无 `ASR_PROVIDER_LOAD_FAILED`）
- [ ] 最低规格目标机（4 核 / 8 GB）手工复测一次 RTF 与峰值内存

## 4. 干净机器安装（中国大陆网络 profile）

在有真实运营商网络（非代理）的 Windows 机器上：

- [ ] 双击 full 包启动，Engine 正常监听 `127.0.0.1`，WebUI 首次设置可完成
- [ ] WebUI /models 安装 Lite：下载经上游源可续传（中断网络再恢复，进度从断点继续）
- [ ] 校验失败场景：篡改下载（hosts 劫持模拟）→ 安装转 failed，无部分激活
- [ ] 源不可达场景：屏蔽 GitHub → 任务保持 waiting/failed 并提示手动恢复，**不**隐式切换下载源
- [ ] hf-mirror 可达性抽查（留证脚本 `scripts/qualify_china_sources.py`）

## 5. 升级 / 卸载

- [ ] 旧版本数据目录上启动新版本：迁移成功、历史任务与 Markdown 报告完好
- [ ] 按 `docs/MIGRATION_ROLLBACK.md` 做一次备份恢复演练
- [ ] 卸载后用户数据默认保留；选择清除后数据目录删除

## 6. 记录

- [ ] 将本机实测体积、RTF、内存数字回填至发布说明（GitHub Release body 草稿）
- [ ] 附上 `benchmarks/results/` 对应报告

## 7. 安装器与 Release（P7）

自动化（每次构建机）：

```bash
uv run python scripts/build_installer.py          # setup.exe + zip + SHA256SUMS
uv run python scripts/verify_release.py dist/evoblue-video-mcp-full   # 步骤 3 需 --models-dir（真实模型安装）
uv run python scripts/verify_p7_acceptance.py     # 真机安装/升级/卸载全链
```

- [ ] `verify_release.py` 全过（本机无真实模型时步骤 3 SKIP；发布机必须带 `--models-dir` 强制执行）
- [ ] `verify_p7_acceptance.py` 16 项全过（安装/token 门禁/双开退出码 3/bridge 握手/重装/卸载保数据）
- [ ] tag == pyproject 版本（release workflow 首个 job 强制）
- [ ] 草稿 Release 附全部产物 + `SHA256SUMS.txt`，Owner 回填体积/RTF 数字后手动发布

## 8. Release 前人工清单（继承 §3-§5）

- [ ] 最低规格目标机（4 核 / 8 GB）干净安装：setup.exe 双击、自启动生效、WebUI 首次设置可完成
- [ ] 中国大陆网络 profile：Lite 模型下载可续传、校验失败转 failed 不半激活（§4 原清单）
- [ ] 升级演练：旧数据目录 + 新安装器 → 迁移成功、出现 `evoblue.db.bak-v<旧版本>`
- [ ] 按 `docs/MIGRATION_ROLLBACK.md` 做一次备份恢复演练
- [ ] 未签名程序的 SmartScreen/杀软放行指引已在 Release notes 与 SUPPORT 引用
- [ ] **杀软实测留证（2026-09-07）**：Windows Defender 云判定把新构建的未签名
      `evoblue-engine-full.exe` 隔离（验收临时目录内 WinError 225）。构建机验收前
      需以管理员加排除：`Add-MpPreference -ExclusionPath '<repo>\dist'`；正式发布
      的安装包哈希不同，用户机器是否被拦需以 Release 后真实反馈为准（处置文案见
      `docs/SUPPORT.md`）


## 9. 审计留档（P8-006）

后果性动作与既有脱敏结构化留档的对应关系：

| 动作 | 留档位置 | 脱敏口径 |
|---|---|---|
| 提交/取消任务、状态转换 | SQLite `jobs`（状态机 + error_code） | error_detail 脱敏（异常类型/稳定码） |
| 报告索引写入/重建 | SQLite `report_documents` + `index_issues` | 路径以数据库记录为准，诊断只出尾部 |
| 模型安装/卸载/下载 | SQLite `model_*` 三表 + 服务日志 | 路径尾部；下载源 host |
| MCP 客户端配置写入/恢复 | 备份文件 + 服务日志（provider_id + 稳定码 + 异常类型） | 不含凭据，不回显文件内容 |
| 设置保存（含报告目录双写） | SQLite `app_settings` + 指针文件 | 目录不进日志 |
| Engine 启动冲突 | stderr 退出码 3/4 + 中文文案（一次性，不落盘） | 只含端口，不含路径 |
| 诊断/导出 | `collect_diagnostics` 统一脱敏（redacted=true） | 路径尾部两段；Key/Cookie 只报已配置/未配置 |
