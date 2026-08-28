# 项目进度 — 2026-08-28

## 当前状态

ASR-4 已完成，P6/ASR 全部收口（ASR-0～ASR-4 done）。基准门禁机制（`asr/release_gate.py` 冻结阈值 v1 + `asr/benchmark` 真实 Provider/RTF/实体召回/峰值内存 + TTS 语料生成器 `scripts/build_benchmark_corpus.py`）真实执行：**Standard SenseVoice 全项通过（zh CER 0.0 / en WER 0.077 / 实体召回 1.0 / RTF 0.06 / 峰值 537 MB），审批为正式默认模型；Lite Zipformer 实体召回 0.50 < 0.60 未过门禁**（英文品牌名场景弱），连同归档无许可证文件，保持「可安装、可显式选择、不自动推荐」。审批按精确 `(model_id, version)` 记录在 `asr/approvals.py`，版本变更即失效重审；证据见 `docs/ASR_RELEASE_GATE.md` 与 `benchmarks/results/`。打包矩阵落地：新增 `__main__.py` 生产入口（loopback uvicorn + production token 首启生成持久化 + WebUI 静态挂载 API 优先）、PyInstaller onedir 双变体 **base 47.4 MB / full 120.9 MB（ASR 运行时增量 73.5 MB 实测）**、CI `package` 三 OS 矩阵（仅产物，不发布）；`scripts/verify_release.py` 打包链路验证八项全过（干净档案启动、审批标志出包、production token 401/200、损坏模型下 Engine 存活 + `ASR_PROVIDER_LOAD_FAILED` 稳定错误码）。`docs/RELEASE_VERIFICATION.md`（含中国大陆网络 profile 人工清单）与 `docs/MIGRATION_ROLLBACK.md`（v1–v7 前向迁移 + 备份回滚演练）落地；`THIRD_PARTY_NOTICES.md` 升级为实际锁定依赖清单并随包分发。

P3 已开工，四个合同已冻结并经一轮评审修复（2026-08-28）：Markdown Schema v1.0（`docs/MARKDOWN_SCHEMA.md` 冻结版 + tz-aware datetime 强制）、历史/搜索 REST API（`docs/HISTORY_SEARCH_API.md`，新端点引入 `{"error":{code,message}}` 信封 + `GET /api/index/issues` 诊断明细）、FTS5 Schema/迁移 v8（`docs/FTS5_SCHEMA.md`，自带文本 FTS5 表 + CJK 查询合同）、索引重建行为（`docs/INDEX_REBUILD.md`，唯一重扫算法 + 幂等崩溃恢复）。评审修复轮关闭 5 个 P1 + 2 个 P2：①查询合同改为「空白分词、连续 CJK 词组保持单短语（邻接语义）、词内引号双写转义」，反例「选品 vs 选择产品」实证通过；②`index_issues` open 去重改部分唯一索引 `uq_index_issues_open`（表级 UNIQUE 含 NULL 列无效）；③stale 语义统一为可搜索、保留 FTS 行、计入 verify_fts；④启动对账废除心跳宽限，持久化 `running` 一律孤儿化（单 Engine 绑定端口，启动即证明旧进程已死）；⑤重复 analysis_id 胜者 = 当前文件集排序最小路径，与数据库历史无关（删库收敛）。决策记录见 `docs/adr/0003`（含修订）；`scripts/verify_p3_contract.py` 从合同文档原文提取 DDL 执行，27 项检查全过（含全部评审反例）。文件所有权：P3 拥有 `storage/migrations.py`、`reports/`、`web/` 与上述合同；ASR-4 已收口。下一步按合同实现：迁移 v8 + repositories → Pipeline 索引阶段 + API 端点 → 重建引擎 + PRD P3 验收测试。

P0、P1、P2 已完成：SQLite Job 模型与迁移、状态机转换规则、单 Worker 租约、Worker 执行循环骨架、Engine 启动恢复、WebUI 后端 API 与前端骨架，以及 P2 字幕分析闭环（平台识别、yt-dlp 元数据/字幕、清洗、分块、LLM 综合、Markdown 报告、生产 runtime 生命周期）。YouTube 真实验收通过；Bilibili 无 Cookie 字幕边界留白。

ASR 方向已完成第二次规划校正：执行验证发现约 1.05 GB 的 SenseVoice 完整制品同时包含约 894 MB FP32 与约 228 MB INT8，并非 INT8 推理本身需要 1.05 GB。P6 改为同一 sherpa-onnx 运行时下的两级模型体验：约 63.4 MB 的离线 Zipformer CTC small INT8 为中文 Lite 候选，约 228 MB 的 INT8-only SenseVoiceSmall 为 Standard 候选；两者均须通过项目基准与许可证/中国下载门禁。

ASR-0～ASR-3 已完成。Windows 真实引擎阻塞已定位为 System32 的 ONNX Runtime 1.10 DLL 抢先加载，而非模型或 sherpa-onnx 版本不兼容。ASR-2 已交付安全 Model Manager 与 WebUI；ASR-3 已交付语言感知 Lite/Standard/Whisper 路由、应用级 Provider 固定、非 claimable 的 `waiting_for_model`、显式单模型安装建议、安装后恢复及 Markdown ASR 身份。ASR-3 经四轮验收评审加固：第一轮修复统一 `reconcile_waiting_asr_jobs()`（双门禁恢复，触发点为 Engine 启动/安装完成/保存 CLI）与 `asr_provider` Literal 枚举；第二轮修复 Silero VAD 受管交付（识别归档不含 VAD，现随包分发 + 钉 SHA 注册前 fail-closed 校验）与 Provider 注册的期望状态对账（配置变更替换、文件消失注销、外部注册保留、卸载即时注销）；第三轮修复 Provider 加载异常隔离（构建失败只让该 Tier 保持未注册并缓存已知坏签名，事件路径重试，绝不阻塞 Engine 启动）与注册所有权失配（_MANAGED 记录 owned instance，注销前校验注册表对象，卸载走 `unregister_managed_provider()`），并补齐随包 Silero MIT 完整许可文本（提前完成一项 ASR-4 发布门禁）；第四轮把 VAD 资源发现/读取/校验/materialize 全部包进故障边界（任何资源异常记稳定错误码并返回 None，损坏安装降级为 Tier 未就绪而非 Engine 不可启动），并将 Provider 加载失败日志脱敏为 provider_id + 稳定错误码 + 异常类型。Whisper Base 精确制品已钉死，CLI 仍为用户显式配置的可选 Runtime。下一工作转 ASR-4；在基准和发布门禁完成前，任何 Tier 均不标记为正式默认已批准。

## 最近完成

- [x] ASR-4 全部交付（2026-08-28）：基准门禁机制 + TTS 语料 v1 真实执行（Standard PASS→正式默认审批、Lite FAIL→不推荐）、`asr/approvals.py` 精确版本审批注册表接入 service/handlers/WebUI、PyInstaller onedir base/full 双变体（47.4/120.9 MB，增量 73.5 MB 实测）、生产入口 `__main__.py`、CI `package` 三 OS 矩阵、`verify_release.py` 八项打包链路验证全过、`docs/ASR_RELEASE_GATE.md` 证据与判决、`docs/RELEASE_VERIFICATION.md`、`docs/MIGRATION_ROLLBACK.md`、第三方声明锁定依赖清单入包。后端 pytest 全量 + mypy strict + ruff、前端 lint/test/build 全绿。
- [x] P3 四合同冻结（2026-08-28）：`docs/MARKDOWN_SCHEMA.md` v1.0 冻结版、`docs/HISTORY_SEARCH_API.md`、`docs/FTS5_SCHEMA.md`（迁移 v8 DDL + CJK 查询合同）、`docs/INDEX_REBUILD.md`（验收标准 §7）+ ADR 0003；`scripts/verify_p3_contract.py` 从文档提取 DDL 实测 12/12 通过。实现（迁移 v8、repositories、Pipeline 索引阶段、API 端点、重建引擎）待开工，见 feature_list P3-005~007。
- [x] ASR-3 终验通过（第五轮评审零新阻塞），正式标记 done；独立复核 43 项相关测试（含真实 ASR/生命周期/VAD 故障/注册所有权）与全量 356 项 + mypy strict + ruff 一致，形成 ASR-3 Git 提交边界。
- [x] ASR-3 release hardening 第四轮：`asr/vad.py` 把资源发现（`files()`）、存在检查、字节读取、SHA 校验、`as_file()` materialize 全部包进故障边界——任何普通资源异常（wheel 漏打 assets / 文件不可读 / materialize 失败）记稳定错误码（`ASR_VAD_ASSET_MISSING` / `ASR_VAD_ASSET_HASH_MISMATCH` / `ASR_VAD_ASSET_UNAVAILABLE`）并返回 None，损坏安装降级为 sherpa Tier 未就绪而非 Engine 不可启动；Provider 加载失败日志脱敏为 provider_id + `ASR_PROVIDER_LOAD_FAILED` + 异常类型（不再输出含本机路径/用户名的原始消息与堆栈）；新增 6 个测试覆盖资源包缺失/读取失败/materialize 失败/注册路径存活/稳定错误码/日志脱敏；全量 pytest 356 + mypy strict + ruff 通过。
- [x] ASR-3 release hardening 第三轮：`asr/registration.py` 构建异常隔离——Provider 加载失败（磁盘损坏/引擎不兼容）只让该 Tier 保持未注册并记录已知坏签名（高频 worker 轮询不重试重载，安装完成/设置保存事件路径 `retry_failed_loads=True` 重试），注册发生在 lifespan 之前故绝不阻塞 Engine 启动；`_MANAGED` 改记 signature + owned instance，注销前校验注册表对象仍是本模块构建的实例（外部覆盖不会被误注销），卸载统一走 `unregister_managed_provider()`；新增测试：损坏模型下 Engine 仍启动、whisper 任务恢复而 sherpa 任务保持等待、外部覆盖后输入消失不被注销、已知坏签名不重复重载；补齐 Silero MIT 完整许可文本（`asr/assets/silero_vad.LICENSE` 随包 + THIRD_PARTY_NOTICES 全文）并修正 `ASR_MODEL_LICENSES.md`「基础安装包不含模型权重」旧表述（提前完成一项 ASR-4 发布门禁）。
- [x] ASR-3 release hardening 第二轮：Silero VAD 改为受管依赖随包分发（识别归档不含 VAD；`asr/assets/silero_vad.onnx` + `asr/vad.py` 钉 SHA `9e2449…1fd6`，注册前 fail-closed 校验，缺 VAD 阻止注册而非转写中途失败）；`asr/registration.py` 从「只注册一次」改为期望状态对账（配置签名变化替换旧 Provider、文件消失/CLI 清空注销本模块注册的条目、外部注册保留），`ModelManagerService.uninstall` 即时注销对应引擎；重启生命周期测试改用 Whisper 真实发现链路（空注册表 + 磁盘布局 + CLI 设置 → lifespan 内注册真实 `WhisperCppProvider` 并恢复搁浅任务）；real-engine 补「空 models 目录 → 注册 → 真实转写」测试；`docs/ASR_MODEL_LICENSES.md` 补 VAD 钉 SHA 与打包决策。
- [x] ASR-3 release hardening 第一轮：`asr/service.py` 新增统一 `reconcile_waiting_asr_jobs()`，双门禁（SQLite 安装记录 + Provider 注册）恢复 `waiting_for_model` 任务，注册刷新先于恢复；接入 Engine 启动（修复安装提交与恢复之间崩溃的永久搁浅）、安装完成与 `/api/settings` 保存 `whisper_cpp_executable`（CLI 后配置自动恢复）三个触发点；Provider 文件门禁注册抽到 `asr/registration.py`（消除 service→bootstrap 反向依赖）；`AppSettingsUpdate.asr_provider` 收紧为四值 Literal 枚举（非法值 422，不再能写入后毒化所有 ASR 任务）。
- [x] 统一 Python 包名 `evoblue_video_hub` → `evoblue_video_mcp`，对齐项目名 `evoblue-video-mcp`。
- [x] 新增状态机转换规则 `jobs/transitions.py`：线性主链 + retry_wait 重入 + 终止态无出边，非法转换抛 `TransitionError`。
- [x] 新增 SQLite 持久层 `storage/`：`Job` ORM 模型、版本化迁移（`schema_migrations` + `SCHEMA_VERSION=1`）、异步 engine（WAL + busy timeout）。
- [x] 新增单 Worker 租约 `storage/repository.py`：原子 compare-and-swap 领取、乐观锁、租约过期接管、终止态保护、恢复扫描。
- [x] 修复审查发现的 4 个 P1 缺陷：`advance_job` 增加租约 CAS 校验（旧 Worker 无法推进）、`max_attempts` 生效并原子转 failed、`recover_stale_jobs` 原子化（不覆盖新租约）、Repository 返回不依赖 `expire_on_commit=False`。
- [x] 新增 Worker 执行循环骨架 `runtime/worker.py`：`claim → 阶段 handler 注入 → 连续推进到终止/retry_wait/failed`，阶段处理器可插拔（P2 填真实 pipeline）。
- [x] 新增 Engine 启动恢复 `runtime/engine.py`：`recover_on_startup` 释放过期租约 + 耗尽重试原子转 failed。
- [x] 覆盖状态转换、并发领取、崩溃恢复、终止态、租约易主、重试上限、Worker 循环与启动恢复的单元/集成测试（45 passed）。
- [x] 新增 `docs/ASR_PLAN.md`：定义 ASR Provider、SenseVoice 验证、Model Manager、语言路由、国内下载、许可证与发布门禁。
- [x] 修复 Windows sandbox 因 `.agents` Owner 异常为 `CodexSandboxOffline` 导致的 `setup refresh had errors`。
- [x] 新增 AppSettings 模型与迁移 v2（精确 per-table 迁移），`list_jobs` 与设置 get/save repository。
- [x] 新增 WebUI 后端 API：`/api/jobs`（列表/详情）与 `/api/settings`（GET/PUT），支持依赖注入 session factory。
- [x] 新增前端路由骨架：首页任务列表 + 首次设置页 + Vite `/api` 代理到 loopback Engine（依赖装不上，标记 SKIP_ENVIRONMENT）。
- [x] 覆盖迁移幂等、Worker 循环、启动恢复与 Web API 的单元/集成测试（48 passed）。
- [x] 新增平台字幕获取第一步：平台数据模型、URL 检测（YouTube/Bilibili）、`PlatformAdapter` 协议、VTT/SRT 解析器、yt-dlp 适配器（元数据 + 字幕，测试 mock 无网络）。
- [x] 完成 P2 字幕分析闭环：字幕清洗、确定性分块、LLM Provider 合同（OpenAI 兼容 + Fake）、结构化综合、Markdown 渲染与原子写入（不可覆盖 + 内容寻址 + 冲突副本）。
- [x] 加固：实时租约续期与取消安全点、chunk summary checkpoint（重试复用）、报告崩溃恢复幂等、worker 异常边界脱敏。
- [x] 组装生产 runtime：`POST /api/jobs` 提交去重 + `/cancel`、handler factory、worker 生命周期、迁移 v4、凭据进 keyring、配置指纹含 base_url + 版本。
- [x] 真实验收：YouTube 闭环通过（DeepSeek + 真实视频 → Markdown）；Bilibili 无 Cookie 字幕边界；修复 httpx 超时与 completed 残留 error 字段。
- [x] ASR-0 合同层：`asr/base.py` Provider 协议（含取消/进度回调）+ 归一化 `ASRResult`/`ASRRequest`/`ASRCapabilities` + 稳定错误码。
- [x] 模型 Manifest Schema（`asr/manifest.py`）：sha256 格式、路径安全 slug、已知平台三连拒 + `load_manifest` 全量校验。
- [x] 确定性 `FakeASRProvider` 与基准脚手架（`asr/benchmark/`）：语料 Manifest、CER/WER/边界误差、可复现评分命令；ADR 0002 + `docs/ASR_BENCHMARK.md`。
- [x] 新增双层守卫 `tests/contract/test_asr_no_provider_imports.py`：静态扫描限定核心层不得 import 引擎（豁免 `asr/providers/`）+ 基础依赖不含引擎库。
- [x] ASR-0.1 加固：归一化结果改 Pydantic 强制不变量（非负有限时间、`end >= start`、单调、身份必填）；取消/进度行为合同（`ASR_CANCELLED`、进度 0..1 单调，Fake 真实执行）；边界误差指标修正（除以边界数 + 缺失/幻觉段惩罚）。
- [x] ASR-0.2 加固：归一化结果冻结（`frozen=True` + tuple，构造后不可改回非法状态）；空 reference 幻觉评 1.0（静音/纯音乐不误判满分）；Fake 支持零片段并报完成进度；守卫补字面量动态 import 扫描并修正能力表述。
- [x] ASR-1 真实引擎解阻：修复 Windows 错载 System32 ONNX Runtime 1.10（C API version 27/10 冲突），ASR extra 固定 Windows `onnxruntime>=1.27` 并在导入 sherpa 前注册其 DLL 目录。
- [x] 修复真实测试暴露的 Provider 缺陷：文件 VAD 使用 512-sample 窗口并 `flush()`、`front` 属性读取、sample offset 转秒、流式读取避免整段音频常驻内存，以及调用 `decode_stream()` 后再取结果。
- [x] Lite/Standard 对 272 秒真实中文 WAV 验证通过；模型获取 helper 保留 `bbpe.model` 且拒绝提取未使用 FP32 权重；全量 `ruff`、mypy strict、pytest 199 项通过。
- [x] ASR-1 收尾验收：60 分钟合成输入分段有界单调、运行中取消 + checkpoint/resume 真实验证、Lite/Standard + Silero VAD 许可证清单（`docs/ASR_MODEL_LICENSES.md`）；全量 pytest 201 项通过。
- [x] ASR-2 第一步（冻结合同）：Manifest 加 `redistribution`（upstream_only/mirror_approved/blocked）+ release gate + `is_releasable`；`model_install_state` 表（迁移 v5）+ `ModelInstallState` ORM + `get/upsert_model_state` 读写；全量 pytest 209 项通过。
- [x] ASR-2.1 合同加固：Manifest 及嵌套字段 frozen + tuple、`redistribution` 必填、来源审批（HTTPS + 批准 host，放在 `is_releasable`）、source 体积与制品一致、`files` 文件 allowlist；数据库拆 `model_install`/`active_model`/`model_download` 三表 + `ModelDownloadStatus` 状态机 + `advance_download_operation` revision CAS；示例 Manifest 改用 `example.invalid`；全量 pytest 218 项通过。
- [x] ASR-2.2 合同加固补全：迁移恢复冻结 v5 并新增 v6（drop 旧单表 + 建三表）；受信任 `asr/catalog.py` 制品级审批（fail closed）；制品一致性（所有 source SHA 相同、`sum(files)==installed_size_bytes`、`archive_format`、重复文件名拒绝）；`update_download_progress`（downloading-only + revision CAS + 单调 + 范围）；`activate_installation`（未安装版本不可激活、保持旧 active）；全量 pytest 231 项通过。
- [x] ASR-2 第二步（下载器/安装器）：补 3 个 P1（Catalog 钉 `sha256`/`size_bytes`、verifying 要求下载完整且字节更新仅走 progress、同一模型单非终态操作的 DB 部分唯一索引）+ `archive_format` 加 `raw`；实现 `asr/model_manager.py`（HTTP Range 续传 + size/SHA 校验、bounded staging 解压 + 文件 allowlist + 原子目录晋升）；全量 pytest 239 项通过。
- [x] ASR-2 第二步收尾（编排层 + 故障安全加固）：修复审查 4 个 P1——下载流式哈希并即时截断超长响应；续传合同闭合（校验 `Content-Range`/`If-Range`、捕获 ETag/Last-Modified、`restart_download` CAS 归零磁盘与 DB）；版本目录不可变原子晋升（存在即复用或拒绝，不删除）；归档路径穿越/链接/设备/重复成员显式拒绝。新增 `asr/installer.py` 的 `install_model()` 编排（`is_releasable` → 复用/去重 operation → partial 核对 → downloading+progress CAS+取消 → verifying → installing → 原子晋升 → save/activate → completed，任何失败转 failed/cancelled 脱敏错误码且旧 active 不变）；全量 pytest 267 项通过。
- [x] ASR-2 第二步二次加固（复查 4 个 P1 + 1 个 P2）：并发单执行者（per-model asyncio 锁 + 已激活幂等短路，重复点击不再并发推进同一 operation）；网络中断真实续传（响应头即持久化 validators + 稳定 `(model, version)` partial 路径 + checkpoint 间崩溃信任磁盘恢复而非删除 + 新 operation 继承 validators）；激活/completed 原子事务（`complete_installation` 单事务回滚，尾部失败不改变旧 active）；归档 single-root 严格合同（拒绝空段/`.`/多 root + tar 流式遍历 + 成员数上限）。新增并发、真实中断续传、崩溃恢复、原子回滚、多 root/空段/`.` 拒绝等测试；全量 pytest 276 项通过。
- [x] ASR-2 第二步三次加固（复查 2 个 P1 + 1 个 P2）：stable partial 改分层路径 `.downloads/<model_id>/<version>.part`（消除 `(a-b, c)` vs `(a, b-c)` 的拼接碰撞，二者锁也不同故须隔离）；`_mark_terminal` 前安全 rollback（DB commit 失败导致 pending-rollback 时仍能把 operation 标为 failed，持久不可用仅 best-effort）；`complete_installation` 校验 operation 的 `model_id`/`version` 与目标一致（拒绝“完成模型 A 的 operation 却激活模型 B”）。新增碰撞隔离、commit 失败标终态、模型版本不一致拒绝 3 个测试；全量 pytest 279 项通过。
- [x] ASR-2 第三步（WebUI 模型管理 + 中国源资格验证）：`asr/manifests.py` 内置 Lite/Standard 生产 Manifest（`scripts/measure_manifest.py` 实测钉入逐文件 SHA，Lite 归档 50,536,402 B / SHA `6a71…524c`）；`installer` 多源回退 + `repository.switch_download_source`；`asr/service.py` `ModelManagerService`（后台安装/取消/卸载）；`/api/models` 端点（list/install 202/cancel/uninstall）+ 前端 `/models` 页；Lite 进 `APPROVED_CATALOG`（`upstream_only`）；`docs/ASR_CHINA_SOURCE_QUALIFICATION.md` 留证 hf-mirror 可达可续传但暂不落镜像。
- [x] ASR-2 第三步审查修复（2 P1 + 2 P2，两轮复核）：多源回退切源即从零下载（坏 partial 不跨源续传，`DigestMismatchError` 也回退）；卸载原子改名 `.trash/<model_id>/<unique>` → 提交 DB → 删 trash，DB 删除失败恢复原位，启动 `cleanup_trash` 按 DB 记录判断恢复/删除（消除崩溃孤儿窗口），`reclaimed_bytes`/`pending_reclaim_bytes` 区分实际回收与待清理；`close()` 仅关闭自建 client；后台任务异常捕获并记录。新增坏前缀回退、DigestMismatch 回退、卸载删除失败、DB 删除失败恢复、trash 恢复/删除、owned client 故障注入测试；全量 pytest 306 项通过。
- [x] ASR-3 分层路由闭环：迁移 v7 增加持久 ASR 身份/建议与应用设置；中文/中英日韩粤使用 Standard/Lite，覆盖外语言建议 `whisper-cpp-base`；固定 Provider 覆盖自动路由；缺模型等待且零隐式下载，安装成功恢复；Markdown 记录实际身份；Whisper CLI 通过无 shell 子进程输出 JSON。后端全量、ruff、mypy 与前端 lint/test/build 验收通过。

## 待做（优先级排序）

| # | 事项 | 优先级 | 阶段 |
|---|---|---|---|
| 1 | P3 实现收尾：迁移 v8 + repositories → Pipeline 索引阶段 + API 端点 → 重建引擎 + PRD P3 验收测试 | P1 | P3 |
| 2 | 统一 MCP 返回 Envelope：`docs/MCP_TOOLS.md` 的 `ok` 字段与 Pydantic 输出模型不一致 | P2 | P4 |
| 3 | Bilibili Cookie 认证（降级为增强能力、不做前置；先确认登录后确实存在字幕再验证） | P3 | P2 |
| 4 | 发布前：在最低规格目标机按 `docs/RELEASE_VERIFICATION.md` 跑人工清单（含中国大陆网络 profile 与真实模型下载续传） | P1（P7 前） | P7 |

## 已知问题

- MCP Python SDK v2 为当前稳定线，真正接入放在 P4；P0 仅定义与 SDK 解耦的业务 Schema。
- SenseVoice 的 1.05 GB 完整包包含 FP32 + INT8；生产 Manifest 只能指向 INT8-only 制品，并分别记录实测下载体积与安装体积。
- SenseVoice 精确归档指向 FunASR Model License 1.1，不是 Apache-2.0；Lite Zipformer 精确归档未携带许可证文件。两者的镜像再分发须在 ASR-2 留证审批，未过门禁前只能作为候选。
- Windows 自带/系统安装的旧 `C:\Windows\System32\onnxruntime.dll` 可能抢先于 sherpa 加载；不得把 C API version 报错误判为模型版本。Provider 必须在导入 sherpa 前固定项目 ASR 环境的 ONNX Runtime DLL 目录。

## 阶段路线图

| 阶段 | 状态 | 目标 | 前置依赖 | 验收标准 |
|---|---|---|---|---|
| P0 | 完成 | Harness、边界、合同、最小骨架 | 无 | 合同齐全，骨架可导入，基础验证通过或明确环境缺失 |
| P1 | 完成 | Local Engine、SQLite、Worker、WebUI 框架 | P0 | Engine 可恢复任务，WebUI 可观察任务与设置状态 |
| P2 | 完成 | YouTube/Bilibili 字幕分析闭环 | P1 | 两平台有字幕视频可生成报告，失败有稳定错误码 |
| P3 | 进行中（合同已冻结） | 历史、Markdown、FTS5、索引重建 | P2 | 删除 SQLite 后可从 Markdown 重建可搜索索引 |
| P4 | 计划 | STDIO MCP Bridge 和四客户端 | P1-P3 | 七工具合同测试通过，多客户端不重复执行 |
| P5 | 计划 | WebUI MCP 自动配置与真实握手 | P4 | 配置可备份、合并、验证和恢复 |
| P6 | 完成 | 可插拔 ASR、Lite/Standard 分层、Model Manager、多语言回退 | P2 | 中文用户可按需安装小模型首体验并显式升级；中国下载可恢复且校验；各层默认模型通过对应基准门禁（Standard 过审为正式默认，Lite 未过审不自动推荐） |
| P7 | 计划 | Windows 安装器和 GitHub Release | P1-P6 | 干净 Windows 环境可安装、升级、卸载 |
| P8 | 计划 | 公开测试和 Harness 加固 | P7 | 关键失败模式有自动化护栏与审计记录 |

各阶段的功能范围、非目标、风险与回滚方式见 `docs/PRD.md`。
