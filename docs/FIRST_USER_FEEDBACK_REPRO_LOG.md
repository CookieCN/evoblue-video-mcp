# 首位试用者反馈 — 复现与证据记录（脱敏）

配套计划：`docs/FIRST_USER_FEEDBACK_FIX_PLAN.md`（F0 §2）。本文件只记录脱敏证据：
不复制用户完整配置、Key、token 或 Cookie。每项证据注明「核验方式」，区分
「源码级确认」与「真实发行包复现」。

## 基线

| 项 | 值 |
|---|---|
| 源提交（修复起点） | b902c51（main，2026-09-10 工作区起点，无未提交改动） |
| 用户安装包 | `EvoBlueVideoMCP-0.9.0-beta.5-setup.exe`（SHA256 用户已自行比对官方 SHA256SUMS.txt 一致） |
| 用户环境 | Windows 10 19045 x64，用户级安装，`evoblue-engine-full.exe` |
| 用户客户端 | ZCode（`~/.zcode/cli/config.json` → `mcp.servers`，引擎 exe + `["bridge"]`），7 工具握手通过 |
| 反馈原文 | `Z:/download/反馈.md`（16 项，2026-09-10） |

## 证据台账

| 反馈 | 核验方式 | 结论 | 状态 |
|---|---|---|---|
| #13 llm_api 恒 401 | 源码：`web/app.py` `_llm_status_probe` 为裸 GET（无 Authorization 头）；`application/diagnostics.py::check_llm_api` 只拼 base_url | **源码级确认**：诊断探测不带认证，DeepSeek `/models` 需 Bearer → 有效 Key 也 401。keyring 链路未证实损坏；`llm/http.py` 实际生成请求带 Bearer 头 | 待发行包复核（F1） |
| #14 预填 deepseek-chat 已下线 | 源码：`frontend/src/App.jsx` 三处 `deepseek-chat`（provider 预设 / 初始 state / placeholder） | **源码级确认**。官方现行模型名以执行时实测 `/models` 为准（反馈称 `deepseek-flash`/`deepseek-v4-pro`；计划文本用 `deepseek-v4-flash`，两者不一致，执行时必须以端点实测裁决） | 待发行包复核（F1） |
| #1/#5 打包版 yt_dlp/asr_runtime 误报 | 源码：诊断用 `importlib.metadata.version()`；`packaging/evoblue_video_mcp.spec` 无 `copy_metadata`（yt-dlp / sherpa-onnx / onnxruntime） | **源码级确认**：冻结包无 dist-info → 探测恒 PackageNotFoundError。运行时本体是否可用未证实（用户任务未到抓取阶段） | 待真实包逐项实测（F2） |
| #7 Standard 安装 ARCHIVE_INVALID | 用户侧：curl 同 URL 下载 SHA256/体积与 manifest pin 完全一致（上游与网络正常）；两次复现失败 | **用户侧证据**，引擎管线缺陷定位未完成；候选方向 = 冻结态 tar.bz2 解压依赖（bz2）或解包路径差异 | 待 F2 用源码与 frozen 两条管线对照复现 |
| #11/#16 字幕未触发 / title 恒 null | 源码：元数据 handler 存 `video_metadata` artifact；列表/详情不回填 title/platform | 与 #1 叠加无法区分「yt-dlp 不可用」与「无可用字幕」；需 F2 先证实 frozen 抓取链路，再走 F3 样本归因 | 待复现（F2/F3） |
| #4 清空 Key 按钮不回退 | 源码：`keyProvided` 基于当前输入；`keyAlreadyOk` 允许同 Provider 已存 Key 留空 | 需区分「新用户未存 Key」与「编辑模式留空保旧 Key」两态；前者应禁用，后者是现有合同 | 待 WebUI 复现（F1） |
| #12 编辑模式保存静默失效 | 源码审查未见显式缺陷；用户复现 3 失败 1 成功 | 未定性；候选 = 资源版本陈旧 / 请求发出但 422 被吞 / 表单校验拦截 | 待发行 WebUI 复现（F1） |
| #15 重启后 waiting→cancelled | 用户自述操作存疑；状态合同规定等待态可恢复 | 未定性；需复现正常退出/强杀/显式取消三矩阵 | 待复现（F4） |

## F0 交付物

- `docs/ENGINE_LOGGING.md`（冻结）：文件日志位置/轮转/脱敏/降级合同。
- `src/evoblue_video_mcp/engine_logging.py`：`<data>/logs/engine.log`，
  2 MB × 3 份滚动，RedactingFormatter 兜底脱敏（Authorization 头/`Bearer`/`sk-` 形状/本机
  token 精确串），目录不可写静默降级且绝不阻塞启动；Bridge 链路零改动。
- 诊断导出新增 `settings.engine_log_file`（仅预期位置，redacted tail，不作存在性声明）。
- 护栏：`tests/unit/test_engine_logging.py`（13 测试）+ 导出字段测试；HARDENING_MATRIX 行 16/17。

## 复现会话记录（追加区）

后续每次复现按此格式追加，不回填：

```
### YYYY-MM-DD HH:MM — 反馈#NN — 环境描述
- 包：名称 + SHA256 前 12 位；源码：commit；OS：…
- 步骤 / 预期 / 实际（含 job_id、operation_id、时间点）
- 结论：已修复 / 原行为符合合同 / 未复现 / 延期 + 证据
```

### 2026-09-10 — #13/#14/#4/#12 — F1 代码层修复（源码态，发行包复现待 F6）

- 包：源码工作区（基线 b902c51 + F0）；OS：Windows 10 22631（开发机）。
- **#13 已修复（源码级确认根因 + 修复）**：`_llm_status_probe` 为裸 GET、`check_llm_api`
  只拼 URL。修复后探测带 Bearer（凭据按 settings 行 ref 从 keyring 读，与
  `ProductionHandlerFactory` 同源）；401/403 报「认证失败」；keyring 不可读报
  `keyring_error` 不探测。测试：`test_llm_api_probe_sends_bearer_from_credential_store`
  等 4 项（Bearer 头精确断言 + Key 不泄漏断言）。**注意**：诊断修复后，用户若仍见
  401，则 Key 确实无效——这正好把「诊断误报」与「真坏 Key」区分开。
- **#8 已修复**：新增 `POST /api/settings/test`（合同 CONFIGURATION.md §LLM 连接测试），
  不落库/不写 keyring/不跟随重定向；前端「测试连接」按钮。11 项集成测试。
- **#14 已修复**：预设 `deepseek-flash`（官方文档 2026-09-10 核对：`deepseek-flash`
  为推荐名，`deepseek-v4-flash` 为仍接受的 legacy 名，`deepseek-chat` 已下线；
  计划文档中 `deepseek-v4-flash` 的写法不采纳）。旧存 `deepseek-chat` 显示可编辑
  迁移提示，不静默覆盖。
- **#4 定性：原行为符合合同**。用户场景是「已存 Key 的编辑模式」——清空输入后按钮
  保持可保存是「留空 = 保留已存 Key」的既有合同（P7 评审第二轮），页面本有
  「已配置（留空保持不变）」标签。新用户（未存 Key）清空后按钮正确回退禁用
  （前端测试 `refuses to complete setup...` 锁定）。无代码改动。
- **#12 未定论，防御性修复三条**：①前端保存失败现在展示后端 detail（此前只显示
  「HTTP 400」，把可运行门禁的中文原因丢了）；②设置路径 keyring 读/写加 10 s 超时
  （阻塞的凭据库不得挂起 settings API——挂起与「无提示、刷新回退」症状吻合，
  但真实根因仍需发行包复现）；③设置区包 `<form>`，Enter 可提交（此前 Enter 必然无效，
  与「键盘 Enter 提交同样无效」吻合——这不是 bug 而是缺失，已补）。
- 验证：后端 ruff + mypy strict 102 + 相关 60 测试；前端 ESLint + 21 tests + build。

### 2026-09-10 — #7/#1/#5 — F2 源码级根因实锤与修复（真实归档复现）

- 包：源码工作区（b902c51 + F0/F1）；复现材料：真实 Standard/Lite 归档自 manifest 钉住的
  URL 下载，归档 SHA256/体积与 pin 完全一致（与用户 curl 结论互证）。
- **#7 根因（两层，均与 frozen 无关，源码环境同样必败）**：
  1. manifest 白名单不全：真实 Standard 归档含 10 个文件成员（README.md/LICENSE/
     export-onnx.py/test_wavs×5 + 权重/tokens），manifest 只声明 2 个 →
     `ArchiveError: undeclared file in archive: 'README.md'`（源码复现第 1 次失败）。
  2. 解压器平铺假设：补全清单后暴露 `nested path rejected: 'root/test_wavs/ko.wav'`——
     `_member_target` 只允许「单根目录+平铺文件」。Lite 归档同样含 test_wavs/×3，同样必败
     （用户未测 Lite）。
- **frozen 假设否定**：beta.5 full 包 `_internal` 实测含 `_bz2.pyd`/`_lzma.pyd`，
  「冻结态缺 bz2 解压依赖」不成立；且两层失败均在源码环境复现。
- 修复：①manifests.py Standard/Lite 白名单补全（10/6 文件，逐文件 SHA 从验证过的归档
  实测钉入；installed_size 同步 = sum(files)，合同校验自洽）；②`_member_target` 允许单根
  目录下嵌套子路径（绝对/盘符/`..`/空段/`.` 守卫不变，新增深度上限 8 段；`_stream_copy`
  本就支持嵌套落盘，嵌套成员仍须在白名单内，无逃逸面）；③installer 失败分支写脱敏文件日志
  （model/version/code/reason——归档相对成员名，无绝对路径无凭据），SUPPORT 增加
  ARCHIVE_INVALID 处置行。
- **源码验收**：两真实归档 `install_archive` 成功——Standard 10 文件 240,506,435 B、
  Lite 6 文件 63,352,462 B，均与 manifest 精确相等。
- **#1 修复**：`check_yt_dlp` 改为模块可用性探测（import yt_dlp 为准；版本展示
  dist-info → `yt_dlp.version.__version__` 兜底），frozen 无元数据不再误报「不可用」；
  spec 增加 `copy_metadata("yt-dlp")`（双变体）与 sherpa-onnx/onnxruntime（full），
  修复 #5 的 asr_runtime 版本 detail 误报。回归测试：模块探测 2 项 + spec 元数据
  （frozen 验证见下）。
- 环境插曲（记 experience）：venv 的 cryptography 被杀软啃掉 `__init__.py`/`_rust.pyd`
  （dist-info 残留导致 uv sync 认为完好）；PyInstaller 在独立 `package` extras，
  `uv sync` 漏 extras 会静默卸载。构建命令必须带 `--extra package`。

### 2026-09-11 — #7/#1/#5 — F2 frozen 实证（重建包）

- 包：本机重建 base（60.0 MB）/ full（133.7 MB，delta 73.7 MB 与历史一致），含 F0-F2 全部代码。
- **#7 frozen**：`tmp-repro/verify_frozen_install.py` —— 打包引擎（隔离数据目录/端口/token）经
  `POST /api/models/{id}/install` 完成真实归档安装（预置验证过的 .partial 跳过网络下载阶段，
  网络可达性已由用户 curl 与本轮归档下载双重证明；从零联网下载链路留 F6 实机矩阵）：
  - Standard：installed/active/completed，10 文件（含嵌套 test_wavs）落位，Provider
    `sherpa-onnx-standard` 注册；
  - Lite：installed/active，6 文件落位，Provider `sherpa-onnx-lite` 注册（仍不自动推荐不变）。
- **#1/#5 frozen**：`yt_dlp: pass | yt-dlp 2026.8.19`（版本从打包元数据读出）；full 的
  `asr_runtime: pass | sherpa-onnx 1.13.6 / onnxruntime 1.29.0`（不再显示「未安装」）；
  base 冒烟 `yt_dlp: pass` + `asr_runtime: warning 运行时未安装, 字幕模式不受影响`（正确的降级文案），
  overall=warning 而非 fail。
- **F0 顺带实证**：frozen `logs/engine.log` 落地，首行为启用记录。
- `scripts/verify_release.py dist/evoblue-video-mcp-full`：PASSED（健康/模型清单/审批标志/
  生产 token 401+200/frozen bridge 握手）。
- 结论：#7 已修复（源码+frozen 双管线）；#1/#5 已修复（full/base 实测）；真实音频转写链路归 F6。

### 2026-09-11 — #3/#9/#11/#16 — F3 代码交付与真实样本归因

- **#11 因果链闭合（真实样本）**：B 站 BV1GJ411x7h7（用户反馈中卡在 waiting_for_model 的任务）——
  adapter 真实抓取：元数据成功（title「【官方 MV】Never Gonna Give You Up - Rick Astley」），yt-dlp
  明示 `Subtitles are only available when logged in` → SUBTITLE_MISSING（查询成功、无可用字幕）→
  auto 模式按合同回退 ASR → 被 #7（模型安装必败）卡在 waiting_for_model。**字幕管线本身工作正常**，
  用户观察到的"字幕路线没走到"= B 站登录字幕墙 + #7 阻塞的叠加。
- **YouTube 样本**：dQw4w9WgXcQ / jNQXAC9IVRw 元数据成功；字幕轨道存在（_pick_subtitle_url 选中
  en SRT URL）；timedtext 下载被 YouTube 匿名限流（HTTP 429，两次不同样本、间隔重试均复现）→
  SUBTITLE_UNAVAILABLE retryable 翻译正确。**真实 found 路径抓取记为验收缺项**（需 F6 实机/带
  Cookie 环境补测；管线 found 路径由 _FakeAdapter 集成测试锁定）。
- **交付**：迁移 v9（title/platform/subtitle_probe）；StageOutcome.display_update 机制（handler 改
  ORM 脏属性在 Core CAS 路径不落库——experience #37）；阻塞原因 blocked_reason/blocked_message
  （稳定串 + Engine 合成指引，实际地址无 token）；MCP 列表双源过滤 + url 回退；ASR_DISABLED 附
  条件性 Cookie 提示。合同：MCP_TOOLS §2/§4、JOB_STATE_MACHINE v9 节。
- 验证：全量 756 passed + 7 skipped 两轮 + ruff + mypy strict 103 + 前端 lint/21 tests/build；
  真实样本 adapter 探测如上。




### 2026-09-11 — #15/#9/#16 — F4 重启恢复与完整任务查询

- **#15 源码级穷尽排查**：全库仅两个 `status=cancelled` 写入方——`request_cancellation`
  （Web `/api/jobs/{id}/cancel`，用户动作）与 `mark_cancelled`（worker 安全点）。启动恢复
  （`recover_stale_jobs` 只释放过期租约、`fail_exhausted_retries` 只动 retry_wait）与
  `reconcile_waiting_asr_jobs`（只恢复不取消）都不会取消等待任务。**WebUI 没有任务取消按钮**
  （仅模型下载有取消），用户侧取消只能来自 MCP 客户端 `cancel_analysis`。
- **坐实的真实竞态（复现为测试）**：取消请求落在 handler 两个安全点之间 → 任务随后合法转入
  `waiting_for_model`（`advance_job` 不检查 `cancel_requested_at`）→ 取消悬挂无人消费（等待态
  不可被 worker 领取）→ 模型安装后被"复活"，直到下一个安全点才落 cancelled。用户视角 =
  「任务停在等待，后来无人取消却变 cancelled」——与 #15 症状一致；「重启」不是必要条件，用户
  观察到的时序关联不构成因果。**定性：悬挂取消竞态 + 旧版列表滤空叠加，非重启本身取消任务。**
- **#15 列表侧缺陷坐实**：`merge_job_pages` 无条件用 `_is_active` 过滤 `/api/jobs` 条目——
  显式 `status=failed/cancelled` 查询被滤成空页（旧测试 `test_status_filter_gates_history_source`
  甚至把空页锁定为预期）。失败/取消历史对 MCP 客户端不可见、不可归因。
- **交付**：停靠事务消费取消（`advance_job` 转向 cancelled）+ 启动清扫（
  `land_dangling_cancellations`，迁移安全消费旧版遗留悬挂行）+ `resume_waiting_jobs` 排除带
  取消标记的行（不得复活）；listing 重写（默认含失败/取消历史、显式终态穿透、
  `error_code`/`url` 字段、单源 total 取服务端精确计数）；WebUI 状态筛选 chips（服务端精确
  查询，aria-pressed）+ 失败/取消归因徽标。合同：JOB_STATE_MACHINE「取消」节三条新纪律、
  MCP_TOOLS §4 F4 修订；HARDENING_MATRIX 行 18/19。
- 重启矩阵（test_engine_recovery + test_worker_loop）：等待任务重启后存活不取消不恢复；悬挂
  取消启动清扫落 cancelled；显式取消的任务在模型就绪后不复活；带标记行 resume 跳过。
- 验证：全量 764 passed + 7 skipped 两轮 + ruff + mypy strict 103 + 前端 lint/23 tests/build。

### 2026-09-11 — #2/#10 — F5 下载入口与模型按钮收口

- **#2 源码级确认**：README 旧文案「从 GitHub Releases 下载」未给链接；仓库唯一 Release
  v0.9.0-beta.5 是 prerelease，GitHub `/releases/latest` 在只有 prerelease 时显示
  「没有任何 releases」——新用户按 README 走会误判项目从未发布。**不通过把 prerelease
  转正解决**（发布是商业动作，Owner 手动）；README 改为直链 `/releases` 全部版本页
  （辅以 `/tags`），明示当前测试版 0.9.0-beta.5 与两个产物名实例，并解释 latest 入口
  为何暂空。顺带校正「交付中：P8」过期状态（P1–P8 + ASR 全链路已交付）。
- **#10 幂等核查结论**：后端 `ModelManagerService.install` 本就是「start (or join)」语义，
  `_is_running` 检查与 `_start` 之间无 await（单事件循环内原子），并发双击不会创建重复
  下载操作——**后端幂等已足够，无需改动**。缺的是前端：POST 在途期间按钮可再次点击。
- **#10 前端修复**：①操作按钮改为**单一可变形按钮**（安装/取消/卸载同一 DOM 元素随状态
  变形）——焦点在状态切换时不再丢失（旧实现三个条件分支按钮互相替换，「安装」点击后
  消失换「取消」，辅助技术/自动化跟丢焦点即反馈所述现象）；②`pendingAction` 在途禁用
  （双击=恰好一次 POST，测试用受控 pending fetch 锁定，不依赖 microtask 时序）；③状态
  徽标 `aria-live="polite"`（状态词进朗读区，进度百分比移出——每 2s 轮询变化否则刷屏）；
  ④页面文案对齐实际行为：「点击「安装」并在确认框中确认后立即开始下载」替换与实际不符
  的「安装前会先展示体积」；⑤安装/取消/卸载失败透传后端 detail（复用 F1 responseDetail）。
- 验证：前端 lint/26 tests/build（模型页新增 3 项：双击单发/按钮变形+aria/错误透传）；
  后端无代码改动，全量回归 764 passed + 7 skipped + ruff + mypy strict 103 + init.sh exit 0。

### 2026-09-11 — 全部 16 项 — F6 修复包交付门禁（真机矩阵）

产物（本机构建，`dist/`）：`EvoBlueVideoMCP-0.9.0-beta.5-setup.exe` 48.5 MB（SHA256
d55cafa3109c030072744580016ab4265b1a6ccf2fd87bfb969a56600694c724）、
`evoblue-video-mcp-0.9.0-beta.5-win-full.zip` 61.3 MB（SHA256
bfaa4e528fe26a2a967e5c330dd27e6299e8a4bbea3fde56ff4731c71ff53ff1）；
原 beta.5 产物留档 `tmp-repro/beta5-artifacts/` 供升级对照。

**门禁链**：ruff ✓、mypy strict 103 ✓、pytest 764+7 skip ✓、前端 lint/26 tests/build ✓、
build_package（base 60.0 MB / full 133.7 MB）✓、`verify_release`（健康/审批标志/生产
token/损坏模型隔离/frozen bridge 握手）PASSED ✓、`verify_p7_acceptance`（真机安装 16 项）
PASSED ✓（脚本编码密闭化修复后）。

**真机七场景矩阵**：

| 场景 | 结果 | 脚本 |
|---|---|---|
| 干净安装（真机 setup） | P7 16/16：安装/版本记录/token 门禁/双开退出码 3+中文提示/bridge 握手/重装/卸载保数据 | `scripts/verify_p7_acceptance.py` |
| 原地升级 beta.5→修复版 | 18/18：v8 库→v9 迁移 + `evoblue.db.bak-v8` 快照、设置与凭据保留、等待任务保留不取消、pre-v9 行 title 诚实 null + url 回退、升级实例上装模型、**卡住的等待任务端到端复活**（真实转写→LLM 阶段诚实失败） | `tmp-repro/verify_f6_upgrade.py` |
| 公开字幕链路两平台 | B 站：真实元数据（title/platform）+ probe=none（登录墙归因）；**YouTube：probe=found，字幕真实下载，管线推进到 cleaning**——F3 记录的「YouTube found 实机缺项」就此闭合 | `tmp-repro/verify_f6_frozen.py` phase 2 |
| ASR 链路 | 验证归档安装 Standard（安装+激活）→ waiting_for_model 自动恢复 → 真实 SenseVoice 转写（provider=sherpa-onnx-standard 钉入任务）→ LLM 阶段失败（无 Key，见缺项） | 同上 phase 5 |
| 自救链路 | 8/8：全量配置保存/读回/测试连接六态（假 Key→auth_failed）/字段缺席保留 Key/显式空串=删除被 runnable 门禁拒绝且 fail-closed/切 Provider 无新 Key 拒绝 | 同上 phase 1 |
| 客户端链路 | frozen bridge 真实调用：七工具注册、list status=failed 终态穿透（total=3）、默认列表含 cancelled 历史、status 报取消消息、失败任务取报告返回 `REPORT_NOT_READY` 稳定信封（绝不伪造报告） | 同上 phase 6 |
| 终止与故障 | 重启后等待任务存活（不取消不恢复）、显式取消跨重启不复活、装模型后取消任务不被复活、engine.log 落盘且假 Key 全程不入日志、损坏模型隔离（verify_release 步骤 3） | 同上 phase 5/7 + verify_release |

**逐项反馈结论（16/16）**：#1 ✓修复（诊断模块探测+打包元数据）；#2 ✓修复（README 直链）；
#3 ✓修复（blocked_reason/message）；#4 定性符合合同（字段缺席=保留/空串=删除，实机八项验证）；
#5 ✓修复（asr_runtime 真实状态）；#6 延期（ZCode 原生适配，计划 §9 P3 不阻塞）；#7 ✓修复
（manifest 白名单+嵌套解压，实机两轮安装）；#8 ✓修复（settings/test 六态）；#9 ✓修复（模型页
指引+title 回填，实机验证指引含实际地址）；#10 ✓修复（单按钮变形/防双击/aria-live）；#11 ✓修复
（probe 三态归因，两平台实机）；#12 防御性修复+实机未复现（F1 三条防护；12 项设置断言全绿，
根因无定论——再复现需用户环境细节）；#13 ✓修复（探测带 Bearer，401=真坏 Key）；#14 ✓修复
（deepseek-flash）；#15 ✓修复（停靠消费取消+启动清扫+不复活，实机三场景）；#16 ✓修复（终态
可查询可归因，REST+MCP 双侧）。

**验收缺项（如实报告，不宣称发布就绪）**：
1. **LLM 报告落盘**：本机无授权 LLM 凭据，两条链路均止步于 LLM 阶段（诚实失败
   `LLM_AUTH_FAILED`）；字幕抓取/ASR 转写/状态机/归因全部实证，仅缺最终 Markdown 生成一步。
   补测条件：提供任一 DeepSeek/OpenAI 兼容 Key。
2. ~~YouTube found 实机~~（已闭合）。
3. ZCode 原生适配（#6）：P3 后续增强。

**附带修复（验收脚本密闭化，Gotcha #8 家族）**：`verify_p7_acceptance.py` 双开检查在
UTF-8 默认 shell 下 reader 线程按 UTF-8 解码引擎 ACP 输出崩溃（stderr=None）；改字节捕获 +
UTF-8→ACP 双解码，两种父环境均密闭。

### 2026-09-11 — 评审 R1–R4 — 修复回合（反例全部翻转）

评审交接：`docs/FIRST_USER_FEEDBACK_REVIEW.md`（4 项确认问题 + 验收缺口）。逐项
「先测试再修复」，反例翻转均以临时还原旧代码验证（测试转红 → 复原 → 转绿）。

- **R1 凭据跨 origin 复用（P1）**：根因 = `settings_test` 复用存量凭据只比 Provider
  名、PUT 门禁同病。修复 = 凭据作用域是**端点 origin**（scheme/host/有效端口；
  host 不区分大小写、默认端口归一）：test 端点跨 origin 留空 → `not_configured` 且
  **不发起任何请求**；PUT 同 Provider 改 origin 留空 → 400（runnable 门禁拒新端点
  复用旧凭据）；跨 origin 显式新 Key 正常；前端 `keyAlreadyOk` 同步 origin 比较
  （按钮重新禁用）。测试 +6（host/port/大写 host/路径不变/显式新 Key/PUT 两侧）。
  合同 CONFIGURATION.md 修订。
- **R2 迟到写入覆盖（P1）**：根因 = `wait_for(to_thread)` 超时后线程照跑且锁已释放，
  「重试收敛」假设不成立。修复 = `_SerialCredentialWriter`（app 级单线程
  ThreadPoolExecutor，生命周期绑 app 不绑请求）：写/删按提交顺序串行；超时只结束
  请求等待（503「尚未确认」）；**`asyncio.shield` 防超时取消排队操作**（无 shield 时
  wait_for 会静默取消未开始的排队写——本轮自踩，修复后「未决仍会执行」语义成立）。
  测试 +2（迟到 set 覆盖新保存 / 迟到 set 复活已删凭据；时序由线程 Event 精确控制）。
- **R3 停靠取消竞态残留（P1）**：根因 = `expire_on_commit=False` 下 `_get` 的实体
  查询命中 identity map 缓存属性——worker refresh 与停靠事务之间提交的取消对
  `advance_job` 不可见。修复 = IMMEDIATE 锁内用**列 SELECT**（`select(Job.cancel_requested_at)`，
  不经 identity map）权威读取消标记。测试 +1（评审原始时序：refresh 后独立
  Session 提交取消 → 停靠 → 断言直接 cancelled、无悬挂）。
- **R4 分页空窗（P2）**：根因 = Bridge 固定拉两源前 100 条本地切片，`offset≥100`
  返回空页；platform/query 只滤首窗。修复三层：① `/api/jobs` 新增 `platform`/
  `query` 服务端过滤（platform 大小写不敏感精确；query 对 title/url 子串，
  LIKE 通配转义）；② 显式非 completed 状态 = 单源查询**全参数下推**
  （limit/offset/platform/query），条目透传、total 服务端精确；③ 默认视图 =
  `_fetch_bounded` 有界深取（100/页 × 上限 50 页/源）后本地三段合并去重切片。
  `completed` 视图跳过任务源（报告历史即全部答案）。测试 +4（下推单调用断言/
  默认深取两页/REST 205 行连续翻页无重无漏 + 服务端过滤精确 total）。
  合同 MCP_TOOLS §4 再修订。
- **验证**：全量 **777 passed + 7 skipped 两轮** + ruff + mypy strict 103 +
  前端 lint/27 tests/build（+1 前端 origin 测试）。
- **验收缺项处置**：
  - 从零联网下载：**闭合**——fresh 数据目录、无预置，frozen 管线 42 s 完成下载→
    校验→10 文件安装→激活（163,002,883 bytes 压缩）。
  - B 站公开 CC 样本：**仍缺**——三轮关键词搜索 + 官方号定向共 14 个候选，匿名环境
    无一可用（空字幕表或登录门控）；B 站字幕对匿名访问普遍门控（正是 #11 的根源）。
    字幕 found 路径已由 YouTube 真实样本闭环（probe=found + 下载 + 管线推进）；
    B 站 found 闭环需带 Cookie 环境或用户提供已知公开 CC 样本，不伪造。
  - LLM→Markdown→FTS 搜索闭环：**仍缺**——本机无授权凭据（保留阻塞事实，不缩写）。
- F6 状态回退 in_progress；HARDENING_MATRIX 行 20/21（R1/R2 失败模式）。

### 2026-09-12 — F6 收官 — LLM→Markdown→FTS 两条链路真实闭环（CLOSED LOOP 14/14）

Wilson 提供真实 DeepSeek 测试 Key（首枚无效被 DeepSeek 官方 401 拒绝——产品「测试连接」
正确分级 auth_failed，恰好实证 F1 #13 修复；第二枚有效，/models 列表同时再次确认
`deepseek-flash` 在列）。Key 仅经环境变量传入验收脚本，全程不出现在任何文件/日志/报告
（脚本断言 engine.log 与全部 Markdown 均不含 Key），验收后 keyring 测试凭据已清理。

**闭环结果**（`tmp-repro/verify_f6_llm_closed_loop.py`，修复后重建的 full 包）：

- **YouTube 字幕链路**：提交→元数据→字幕 found→清洗→分段→真实 LLM 摘要→**completed**；
  Markdown 落盘（2195 字符真实内容）；/api/history 收录；FTS 搜索命中；**frozen bridge
  get_analysis_report 取回真实中文核心摘要**。
- **B 站 ASR 链路**：无字幕（登录墙）→waiting_for_model→Standard 安装激活→自动恢复→
  真实 SenseVoice 转写→真实 LLM→**completed**（asr=sensevoice-small-int8 钉入）；
  FTS 搜索两份报告均可命中（total=2）。
- **附带发现并修复真实缺陷**：首轮 B 站链路在 downloading_audio 一次网络抖动即
  fatal（`AUDIO_DOWNLOAD_FAILED` retryable=false，attempt 1/3 直接 failed；yt-dlp 直连
  三次复现全成功证实为暂时性）。根因 = 分类器把「无法分类的 DownloadError」默认
  不可重试。修复：音频下载阶段（此时视频已确认可访问）的 DownloadError 恒
  retryable=True——重试预算兜底罕见的永久性情况；transcode 失败仍不可重试（本地
  ffmpeg 问题重试无益）。测试 +2（含「无可识别原因也必须可重试」锁定）。
- 验证：全量 **779 passed + 7 skipped** + ruff + mypy strict 103 + adapter 测试 18 项。

**F6 缺项清单最终状态**：LLM→Markdown→FTS 闭环 **闭合**；从零联网下载 **闭合**（42s）；
**B 站公开 CC 字幕样本仍登记为缺**（平台匿名门控，14 候选探测无一可用）——解锁需
带 B 站登录 Cookie 的环境或已知公开 CC 样本链接；B 站平台管线已由 ASR 链路完整验证。

### 2026-09-12 — 第二轮评审 R1b/R4b — 修复回合（反例翻转）+ 闭环归属断言补强

评审交接：`docs/FIRST_USER_FEEDBACK_REVIEW_ROUND2.md`（R1b 凭据绕过 P1、R4b 分页截断
P2、闭环断言补强与状态同步要求）。上轮 R2/R3 修复本轮确认无残留。

- **R1b setup=false 绕过凭据 origin 边界（P1）**：根因 = 上轮修复的比较基准是「当前
  存储的 base URL」——用户先改地址（setup 关闭路径绕过 runnable 门禁）再测试/重开，
  比较对象已被污染，旧凭据照发新站点。修复 = **凭据与其保存时的端点持久绑定**：
  迁移 v10 新增 `app_settings.llm_credential_origin`（存量行回填当时的 base URL）；
  修改 origin 或切换 Provider 而未提供新 Key 的保存**原子解除绑定**（旧凭据留在
  凭据库但不再被引用），该决策独立于 setup_completed；settings/test 的复用基准改为
  记录的 origin（且要求绑定存在）；前端 `keyAlreadyOk` 同步比较 `llm_credential_origin`。
  开启态改 origin 未带 Key 仍被 runnable 门禁拒绝（400），关闭态改 origin 解绑保存
  （200）——两路径语义连贯。测试 +2（评审三序列：同请求关+改、先关后改、重开拒绝）
  + 迁移 v10 回填测试 + 夹具更新，反例翻转验证。
- **R4b 历史查询截断阈值 5000（P2）**：根因 = `_fetch_bounded` 固定 50 页深取后本地
  切片——总数 5001 时第 5001 条不可达且每次调用最多发 50 个请求。修复 = **全部视图
  真下推**：`completed` 直连 `/api/history`（`limit`/`offset`/`platform`/`query` 下推，
  history 端点新增 `query` 服务端过滤——title/source_url 不区分大小写子串，LIKE
  通配转义）；默认视图改**双段服务端分页**——`/api/jobs` 新增 `status_group=
  non_completed`（未完成任务段，失败/取消随行，提交时间倒序），全局 offset 映射到
  该段与历史段，每次调用恰好 1-2 个引擎请求；`_fetch_bounded` 删除，无固定上限；
  工具总预算由单调 deadline 守护（每请求超时 = min(单请求上限, 剩余预算)，预算耗尽
  映射 ENGINE_TIMEOUT 而非伪装空页）。listing 重写为 `single_source_page`/
  `completed_page`/`merge_default_pages`（纯拼接+页内去重+双段 total）。测试 +2
  bridge（completed 5001 深分页单请求 / 默认视图段映射两请求）+ 跨段窗口测试 +
  REST status_group/query 测试；旧「本地过滤/本地切片」前提的 5 个 listing 测试按新
  合同重写。反例翻转验证（实现前两个 bridge 测试均红）。
- **闭环断言补强**：搜索命中改**精确 job_id 断言**（两个样本同一首歌，旧的
  total>=1 可被另一条报告满足）；新增**每份 Markdown 的 `analysis_id` frontmatter
  归属核对**；重建包后完整重跑：两链路 + 全部新断言 PASS（含归属离线复核：
  analysis_id 集合精确等于两个 job_id）；「真实中文摘要」表述修正为「取回真实摘要」
  （语言非验收要求，未断言）。
- **音频重试取舍的诚实口径**：`AUDIO_DOWNLOAD_FAILED` 恒可重试是**有界重试取舍**
  而非精确错误分类——「元数据成功」不证明音频必然可访问，永久性故障（会员/地区
  音频、文件损毁）同样会耗尽重试预算后以 failed 终止；收益是网络抖动不再一次致命。
- **验证**：全量 **785 passed + 7 skipped** + ruff + mypy strict 103 + 前端 lint/27
  tests；重建 full 包（含 R1b/R4b/音频重试）闭环重跑通过。
- **仍登记的缺项**（不缩减）：B 站公开 CC/带登录字幕的成功路径（ASR 成功不证明该
  平台字幕提取成功；需 Cookie 环境或样本链接）。

## 2026-09-14 第三轮评审修复（docs/FIRST_USER_FEEDBACK_REVIEW_ROUND3.md）

- **R5 凭据槽与数据库分裂（P1）**：根因 = keyring 按名字寻址，PUT 先原地覆盖
  `llm:{provider}` 槽、SQLite 后提交——评审实测「写成功 + DB 强制失败」后数据库仍
  指向旧端点而槽内已是新 Key（B 的凭据会发给 A、A 的凭据丢失）；删除路径同理
  （先删槽后提交失败 → 绑定引用已删除的槽）。修复 = **槽版本化 + prepare→commit→
  cleanup**：每次写入全新槽 `llm:{provider}:{12hex}`（`new_llm_credential_reference`），
  DB 提交是唯一切换点；提交成功后 fire-and-forget 清理被替换槽（同串行队列保序）；
  超时 503 的迟到写落入无人引用槽、其清理排在写之后；删除反向（先 DB 解绑后清槽）；
  取消路径不清理（取消可落在 commit await 后而 SQLite 已采纳新 ref——删槽反破坏）；
  存量 `llm:{provider}` 读取透明、写入时自动迁移清理。**断言全部经数据库 ref 实际
  读取 secret**（评审明确要求，不查 PUT 响应）。测试 +5（DB 失败保旧绑定 / 超时迟到
  写不可改绑 / 删除先解绑 / 版本化替换+清理 / 存量迁移）；翻转：槽名退化共享 → 4 红。
- **R6 默认视图 total 漏历史 + 跨页重复（P1）**：根因 1 = 任务段满页时不请求
  history（`history_body=None` → history_total=0），任务段 2 + 历史 5001 时 total=2，
  客户端在段边界停止翻页；根因 2 = 「索引已提交但 job 非 completed」合法双存
  （IndexingHandler 先提交、worker 下一笔事务才推进 completed），页内去重救不了
  跨页（评审实测相邻两页同 job、total 1→3）。修复 = **服务端严格不相交**：history
  新增 `exclude_unfinished_jobs=true`（`analysis_id NOT IN (SELECT job_id FROM jobs
  WHERE status != 'completed')`，任务行胜出）；bridge 恒 2 请求——满页发 `limit=1`
  计数探测（探测 items 不并入页，属于后续页）；`merge_default_pages` 去重删除、
  total=两段之和每页精确。测试 +4（满页 total 探测 / 有状态引擎连续翻页无重无漏 /
  过滤后双段 total / 真实 SQLite 不相交反例——failed job + 其报告 + flag 排除）；
  翻转：无探测 → 3 红、无不相交 → 1 红。
- **R7 默认排序混排（P2）**：`status_group=non_completed` 只按 created_at 排，新的
  failed 越过旧的运行中，违反冻结合同「运行中 → failed/cancelled → 历史」。修复 =
  SQL `CASE status IN (completed,failed,cancelled) THEN 1 ELSE 0` 先分组，再
  created_at desc + id desc 决胜（翻页确定性）；history 同步 `analyzed_at desc,
  id desc`。测试 +1（旧运行中 + 新失败/取消，断言精确顺序）；翻转：去 rank → 红。
- **R8 history platform 大小写敏感（P2）**：jobs 用 `LOWER` 而 history 用 `=`，
  `platform=YOUTUBE` 两源结果不同。修复 = `LOWER(platform) = LOWER(:platform)`。
  测试 +1（YOUTUBE/youtube 同结果）；翻转：回 `=` → 红。
- **重写**：R2 两测试按版本化槽语义重写（最终态断言 = DB ref 读秘密 + 凭据库收敛
  空态）；两个旧页内去重 listing 测试改锁定「服务端不相交 + 直通拼接」新合同。
- **验证**：全量 **796 passed + 7 skipped** + ruff + mypy strict 103 + 前端
  lint/27 tests；五项翻转全部红→复原→绿。合同：CONFIGURATION（槽版本化节）、
  MCP_TOOLS §4、HISTORY_SEARCH_API（`exclude_unfinished_jobs` + 排序决胜）、
  HARDENING_MATRIX 22/23。
- **仍登记的缺项**（不缩减）：B 站公开 CC/带登录字幕的成功路径（需 Cookie 环境或
  样本链接）。本轮未使用真实凭据、未重建发行包（改动为源码级，发行验收按发布流程
  于 bump 时执行）。

## 2026-09-14 第四轮评审修复（docs/FIRST_USER_FEEDBACK_REVIEW_ROUND4.md）

- **R9 两次查询无共同快照（P1）**：根因 = jobs 与 history 是两个独立 HTTP/SQLite 快照
  ——job 在两次请求之间由 indexing 被 worker 推进为 completed 时，任务行已在第一段
  返回、报告因状态翻新在第二段合格入列，同 job 双现且 total 双计（评审实测
  items=[dup,dup]、total=3）。修复 = **统一端点 `GET /api/jobs/unified`**：单一 SQLite
  读事务内 1 行探测钉住 WAL 快照 → 两段计数、`exclude_unfinished_jobs` 排除、
  active→terminal→历史排序、段映射切片全部同快照；Bridge 默认视图单请求透传
  （`listing.unified_page`，`kind` 标记段），双请求编排与 `limit=1` 探测删除。反例
  测试：monkeypatch `list_report_documents` 在历史读取前从独立连接提交
  dup→completed——统一端点回答仍单次出现、total=2。翻转验证：jobs 读取后插
  `sess.commit()`（模拟两快照）→ 测试转红；复原转绿。bridge 默认视图 5 个测试按单
  请求合同重写。
- **R10 durable commit 后取消造成指针分裂（P1）**：根因 = 取消可落在 commit await 上
  而 SQLite 已持久化（`immediate_write_transaction` 的 shield verdict 语义：保留写入、
  传播取消），settings PUT 的 CancelledError 处理器无条件恢复旧指针 → 库=new-reports、
  指针=old-reports。修复 = 事务助手新增 `on_commit` 同步回调（durable 两条路径触发：
  正常 await 与取消 verdict 后）；设置保存按 verdict 补偿——已提交：不动指针 + 清理
  被替换凭据槽；未提交：恢复指针（`pointer_replaced` 标志保证只恢复确实替换过的指
  针）。测试以「真实 commit 完成后 raise CancelledError」的包装事务助手注入精确窗口；
  翻转验证：退回无条件恢复 → 转 红；复原转绿。既有指针测试族（DB 失败补偿/清除失败
  阻断/reconcile 期间取消不补偿）全部保持绿。
- **R11 pointer 失败绕过新槽清理（P2）**：根因 = pointer 读/写失败的 503 raise 位于
  DB try 块之前，`_cleanup_unadopted_fresh_slot` 不可达——新 Key 已确认写入但数据
  库未采纳，凭据库同时留旧 Key 与不可见新 Key。修复 = pointer 段并入同一 try，全部
  失败出口统一过清理（HTTPException 原样直通重抛，非 HTTP 异常包装 503）；纯失败路
  径仍恢复指针（仅当 `pointer_replaced`）。翻转验证：HTTPException 提前 raise 绕过
  清理 → 转红；复原转绿。
- **验证**：全量 **798 passed + 7 skipped** + ruff + mypy strict 103 + 前端 lint/27
  tests；4 个新反例先红后绿，三项翻转全部红→复原→绿。合同：MCP_TOOLS §4（R9）、
  INDEX_REBUILD §4（verdict 补偿）、HISTORY_SEARCH_API、HARDENING_MATRIX 行 22/23
  扩展 + 行 24。
- **仍登记的缺项**（不缩减）：B 站公开 CC/带登录字幕的成功路径（需 Cookie 环境或
  样本链接）。本轮未使用真实凭据、未重建发行包（源码级修复，发行验收按发布流程于
  bump 时执行）。

## 2026-09-14 第五轮评审修复（docs/FIRST_USER_FEEDBACK_REVIEW_ROUND5.md）

- **R12 提交前取消遗留新 Key（P2）**：`on_commit` verdict 在所有 durable-success
  路径传播取消**之前**置位——外层捕获取消且 verdict=false 即证明数据库未采纳
  fresh slot。修复 = 未提交取消分支恢复指针之外同时 `_cleanup_unadopted_fresh_slot()`
  （评审要求真实 task 取消：monkeypatch save 在事务体内挂起，`task.cancel()` 后断言
  DB 旧 ref 可读旧 Key、凭据库 drain 后无 sk-new）；翻转（去掉清理调用）转红。
  已提交取消（verdict=true）语义不变：保留新槽、清理被替换槽。
- **R13 畸形统一响应未安全失败（P2)**：`{"unexpected":"shape"}` 旧逻辑返回
  `ok:true,total:0`（静默隐藏全部任务与历史）；item 缺 status 时 `_job_item`
  KeyError 升级协议级 ToolError（绕过 `_validated` 的 ValidationError 降级）。
  修复 = `unified_page` 严格验证：顶层 `items` 列表 + 非负 int `total`、`kind`
  判别（job/history 之外拒绝）、每 item 字段校验（KeyError/ValueError/TypeError
  → `UnifiedResponseFormatError`）；`_list` 捕获后 `_validated(None)` 走既有
  BRIDGE_INTERNAL 降级（isError=false、日志只记工具名）。反例 ×2（顶层缺失、
  item 缺/非法 status）+ 单元级畸形矩阵；翻转（退回宽松解析）两项转红。
- **清理**：`tmp-block1.txt`（废弃双请求测试片段）删除；`.gitignore` EOF 空行
  修复（`git diff --check` 干净）；`merge_default_pages`/`merge_job_pages`
  死代码删除（R9 统一端点后退出生产路径，仅测试引用）；
  `test_listing.py` 按生产入口（single_source_page/completed_page/unified_page）
  重写，删除镜像双请求测试，新增畸形载荷单测与 `test_bridge_tools.py` 两反例。
- **验证**：全量 **798 passed + 7 skipped** + ruff + mypy strict 103；4 个新反例
  先红后绿，两项翻转验证。合同：CONFIGURATION（verdict 取消清理）、MCP_TOOLS
  §4（畸形载荷降级）、HARDENING_MATRIX 行 22/23 追加 R12/R13 测试。
- **仍登记的缺项**：B 站公开 CC/带登录字幕成功路径（需 Cookie 环境或样本链接）。

## 2026-09-14 第六轮评审修复（docs/FIRST_USER_FEEDBACK_REVIEW_ROUND6.md）

- **R14 显式状态视图伪装畸形响应（P2）**：根因 = R13 的 `UnifiedResponseFormatError`
  只包住默认视图；`completed_page`/`single_source_page` 仍 `items 缺失=>[]`、
  `total 缺失=>0`，`_dict_items` 静默丢弃非 object item（评审实测 completed/failed
  对 `{"unexpected":"shape"}` 均返回 ok:true/total:0/isError=false）。修复 = 三源共用
  `_strict_window`：items 列表 + 非负 int total + **limit/offset 窗口回显** +
  **页容量 = min(limit, max(0, total-offset))**；single_source 逐 item 校验状态与
  请求过滤一致（不匹配即违约，废除静默过滤）；非 object item 一律违约。异常统一
  `EngineListFormatError`，`_list` 三个分支同一 `_degrade_to_bridge_internal()`。
- **R15 unified 跨字段不变量缺失（P2）**：修复 = jobs_total/history_total 必填
  （非负 int）且 `total == jobs_total + history_total`；job 段禁 completed（两段
  不相交语义）；history 段必须呈现 completed（声明非 completed 即违约，绝不改写）。
  评审反例全部覆盖：空第一页但 total=7、history status=failed 被改写、completed
  job 被接受、计数缺失/不一致。
- **验证**：反例测试 completed/failed 全工具 ×1 + 非 object item ×1 + 不变量单测
  三组；翻转 ①共享窗口退宽松（去掉回显/容量）→ 显式状态伪装转红（unified 因分段
  检查仍拦截，证明共享边界是锁定目标）②去分段和与状态归属 → 归属测试转红（计数/
  容量反例由容量检查冗余锁定）。既有夹具全部修为合同一致（补窗口回显、分段计数、
  tail-page 容量语义：101 条取 offset=100 的尾页 1 行）。全量 **804 passed +
  7 skipped** + ruff + mypy strict 103 + 前端 27；`_list` 文档说更正为单请求单快照。
- **仍登记的缺项**：B 站公开 CC/带登录字幕成功路径（需 Cookie 环境或样本链接）。

## 2026-09-14 第七轮评审修复（docs/FIRST_USER_FEEDBACK_REVIEW_ROUND7.md）

- **R16 wire 层类型仍在被强制转换（P2）**：根因 = `_job_item`/`_history_item` 用
  `str()`/`int()` 投影——数字 job_id→"123"、布尔 progress→1、列表 title→"['bad']"
  全部 ok:true；REST 必填字段（jobs 行 progress/created_at、history 行完整
  HistoryItem 字段、unified 行 progress）缺失被接受；`limit/offset` 回显等值比较
  误收 bool（`True == 1`）。修复 = **wire 层严格模型** `_WireJobRow`/
  `_WireHistoryRow`/`_WireUnifiedRow`（Pydantic `strict=True` + `extra="ignore"`：
  类型精确匹配、必填齐全、容忍未来引擎新增字段），`_wire()` 统一入口
  （ValidationError → EngineListFormatError），投影函数只搬运不转换；MCP 投影
  越界（如 progress>100 超 MCP le=100 但 REST 合法）同样降级。
  `_strict_window` 显式拒绝布尔回显。
- **R17 未按全局位置校验分段（P2）**：totals 正确 + 行内状态正确 ≠ 切片有序——
  jobs_total=1 第一页返回 [history, job] 仍 ok:true。修复 = 每行按
  `global_index = offset + index` 推导期望 kind（< jobs_total 必 job、否则必
  history），覆盖纯 jobs 页/跨段页/纯 history 页/深 offset。
- **测试卫生（评审点名）**：畸形夹具改为「其余字段完全合法、只变被测字段」——
  旧夹具多缺 jobs_total/history_total/窗口回显，在 `_strict_window`/分段检查
  提前失败，未执行注释声称的 item 路径；bridge missing-status 测试同样重建。
- **验证**：反例（wire 类型矩阵三源 × 数字/布尔/列表/字符串/必填缺失、bool 回显、
  位置正反例：跨段正序 ok/反序 degrade/history-only 页 ok/job 占位 degrade）
  先红后绿；翻转：`model_construct` 直通绕过 wire 校验 → 红、去位置检查 → 红。
  全量 **809 passed + 7 skipped** + ruff + mypy strict 103 + 前端 27。
- **仍登记的缺项**：B 站公开 CC/带登录字幕成功路径（需 Cookie 环境或样本链接）。

## 2026-09-14 发布决定（0.9.0-beta.6）

- 八轮评审通过（ROUND8：无新增 finding，可以提交）；提交 `e7e3803` 已 push。
- **B 站公开 CC 字幕样本缺项：Owner 决定豁免，不阻碍发布**。理由：B 站视频字幕
  多为内嵌硬字幕（烧录在画面中，不存在可抓取的独立字幕轨），公开 CC 样本不
  代表真实用户的实际路径；无字幕场景由 ASR 回退链路覆盖，该链路已在
  CLOSED LOOP 14/14 中端到端验证（B 站真实样本：字幕登录墙 → ASR → LLM →
  Markdown → FTS → bridge）。
