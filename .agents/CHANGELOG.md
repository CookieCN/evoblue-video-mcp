# Changelog

## [Unreleased]

### Added

- Silero VAD 改为受管依赖随基础包分发（`asr/assets/` + `asr/vad.py` 钉 SHA 校验）：识别归档不含 VAD，注册前 fail-closed 校验，缺失/损坏时阻止 Provider 注册而不是转写中途失败；`docs/ASR_MODEL_LICENSES.md` 补 VAD 钉 SHA 与打包决策。
- Silero VAD 完整 MIT 许可文本随包分发（`asr/assets/silero_vad.LICENSE`）并全文录入 `THIRD_PARTY_NOTICES.md`；修正 `docs/ASR_MODEL_LICENSES.md` 中「基础安装包不包含任何模型权重」的旧表述（现为仅随包附带 VAD）。
- ASR-3 release hardening：统一 `reconcile_waiting_asr_jobs()`（双门禁：SQLite 安装记录 + Provider 注册），接入 Engine 启动、安装完成与保存 whisper CLI 路径三个恢复触发点，消除安装提交后崩溃与 CLI 后配置导致的 `waiting_for_model` 永久等待；文件门禁 Provider 注册抽至 `asr/registration.py`。
- `PUT /api/settings` 的 `asr_provider` 收紧为 `auto`/`sherpa-onnx-lite`/`sherpa-onnx-standard`/`whisper-cpp-base` Literal 枚举，非法值 422 拒绝。

### Changed

- `asr/registration.py` 改为期望状态对账：CLI 更换/清空、模型文件删除时替换或注销对应注册（仅限本模块注册的条目，外部注册不受影响），`ModelManagerService.uninstall` 即时注销对应引擎。
- Provider 加载异常隔离：构建失败只让该 Tier 保持未注册（记录脱敏错误 + 已知坏签名避免高频轮询重复重载），安装完成/设置保存事件路径显式重试，绝不阻塞 Engine 启动；`_MANAGED` 记录 owned instance，注销前校验注册表对象归属，卸载统一走 `unregister_managed_provider()`。
- VAD 资源访问全部包进故障边界：资源缺失/不可读/materialize 失败记稳定错误码（`ASR_VAD_ASSET_MISSING` / `ASR_VAD_ASSET_HASH_MISMATCH` / `ASR_VAD_ASSET_UNAVAILABLE`）并返回 None，损坏安装降级为 Tier 未就绪而非 Engine 不可启动；Provider 加载失败日志脱敏为 provider_id + `ASR_PROVIDER_LOAD_FAILED` + 异常类型。

- 统一 Python 包名 `evoblue_video_hub` → `evoblue_video_mcp`，对齐项目名 `evoblue-video-mcp`。
- 新增 SQLite Job 模型（`storage/models.py`）、版本化迁移（`schema_migrations` + `SCHEMA_VERSION`）。
- 新增状态机转换规则（`jobs/transitions.py`），非法转换抛 `TransitionError`。
- 新增单 Worker 租约闭环（`storage/repository.py`）：原子 compare-and-swap 领取、乐观锁、租约过期接管与恢复。
- 新增覆盖状态转换、并发领取、崩溃恢复、终止态保护与迁移幂等的测试。
- 新增 Worker 执行循环骨架（`runtime/worker.py`）：`StageOutcome`/`StageHandler` 协议与 `run_worker_once` 连续推进。
- 新增 Engine 启动恢复（`runtime/engine.py`）：`recover_on_startup` 释放过期租约并失败耗尽重试。
- 新增 `docs/ASR_PLAN.md` 与 Harness 交接入口，定义 ASR Provider、模型 Manifest、SenseVoice 验证、Model Manager、中国下载链路及发布门禁。
- 新增项目交接文件 `Codex.me`，并将其纳入 `init.sh` 必备文件检查与开工读取顺序。
- 新增 AppSettings 模型与迁移 v2（精确 per-table 迁移），`list_jobs` 与设置 get/save repository。
- 新增 WebUI 后端 API（`/api/jobs` 列表/详情、`/api/settings` GET/PUT）与前端路由骨架（任务列表 + 首次设置 + Vite loopback 代理）。
- 新增平台字幕获取（`platforms/`）：URL 检测、`PlatformAdapter` 协议、yt-dlp 适配器（元数据 + 字幕）、VTT/SRT 解析器。
- 完成 P2 字幕分析闭环：字幕清洗、确定性分块、LLM Provider 合同（OpenAI 兼容 + Fake）、结构化综合、Markdown 渲染与原子写入（不可覆盖 + 内容寻址 + 冲突副本）。
- 组装生产 runtime：提交去重 + 取消 API、handler factory、worker 生命周期、迁移 v4、凭据进 keyring、配置指纹含 base_url + 版本。
- 加固：实时租约续期、取消安全点、chunk summary checkpoint、报告崩溃恢复幂等、worker 异常边界脱敏。
- 新增 ASR Provider 合同层（`asr/`，ASR-0）：`ASRProvider` 协议、归一化 `ASRResult`/`ASRRequest`/`ASRCapabilities`、稳定错误码。
- 新增模型 Manifest Schema（`asr/manifest.py`）：校验 sha256、路径安全 slug、已知平台，`load_manifest` 全量校验。
- 新增确定性 `FakeASRProvider` 与基准脚手架（`asr/benchmark/`）：语料 Manifest、CER/WER/边界误差评分、`python -m evoblue_video_mcp.asr.benchmark` 可复现命令。
- 新增 ADR 0002（可插拔 ASR 与带外模型交付）与 `docs/ASR_BENCHMARK.md`。
- 新增 ASR-1 sherpa-onnx Provider、Lite/Standard 注册、真实引擎集成测试、分段 checkpoint/resume 接线与 Windows ONNX Runtime 显式依赖。
- 完成 ASR-1 验收：60 分钟合成输入分段有界单调、运行中取消 + checkpoint/resume 真实验证、Lite/Standard + Silero VAD 许可证清单（`docs/ASR_MODEL_LICENSES.md`）。
- 新增 ASR-2 第一步合同：Manifest `redistribution` 三态（upstream_only/mirror_approved/blocked）+ release gate；`model_install_state` 表（迁移 v5）与下载状态读写。
- 加固 ASR-2 合同（ASR-2.1）：Manifest 全不可变 + `redistribution` 必填 + 来源审批 + 文件 allowlist；拆 `model_install`/`active_model`/`model_download` 三表 + 下载状态机 + revision 乐观锁。
- 补全 ASR-2 合同（ASR-2.2）：迁移 v6（保留冻结 v5）+ 受信任 Catalog 制品级审批（fail closed）+ 制品一致性（SHA 一致/sum(files)/archive_format/重复文件）+ `update_download_progress` 进度 CAS + `activate_installation` 原子激活。
- 实现 ASR-2 下载器/安装器：Catalog 钉制品 SHA/size、verifying 要求下载完整、同一模型单非终态操作 DB 约束、`raw` 制品格式；`model_manager.py` 提供 HTTP Range 续传 + 校验、bounded staging 解压 + 文件 allowlist + 原子晋升。
- 完成 ASR-2 第三步（WebUI 模型管理 + 中国源资格验证）：`asr/manifests.py` 内置 Lite/Standard 生产 Manifest（钉入实测逐文件 SHA）、`asr/service.py` 模型管理服务（后台安装/取消/卸载 + `ModelSummary`）、`/api/models` 端点（list/install/cancel/uninstall）、前端 `/models` 页（体积先行同意、进度、取消、卸载）。
- 实现 Manifest 多源回退：`installer` 按 `sources` 顺序下载，某源失败自动切换下一源（切源即从零下载），`repository.switch_download_source` CAS 更新；Lite Zipformer 进入 `APPROVED_CATALOG`（`upstream_only`）。
- 新增 `docs/ASR_CHINA_SOURCE_QUALIFICATION.md` 留证（hf-mirror 实测可达 + 支持 Range + 散文件体积与钉死值一致，但「暂不落镜像」）与 `scripts/qualify_china_sources.py` 只读验证脚本、`scripts/measure_manifest.py` 制品测量脚本。
- 完成 ASR-3：新增语言感知 Lite/Standard/Whisper 路由、应用级 Provider 固定、持久化 `waiting_for_model` + 单模型安装建议、安装成功自动恢复等待任务，且全程禁止隐式下载。
- 新增可选 `WhisperCppProvider`（无 `shell=True` 的 CLI JSON 适配器）与钉死的 `whisper-cpp-base` raw Manifest；模型与 CLI 均不进入基础安装包。
- Markdown Schema v1 增加 `asr_provider`、`asr_model`、`asr_model_version` 可选身份字段，生成报告记录实际识别链路。

### Changed

- 后端验证从 `evoblue_video_hub` 迁移到 `evoblue_video_mcp` 包路径。
- P6 从单一 Faster-Whisper 方案调整为可插拔本地 ASR：SenseVoiceSmall INT8 为中文优先候选，whisper.cpp/faster-whisper 为按需兼容能力；基础安装包不携带模型权重。
- P6 中文 ASR 调整为 Lite/Standard 两级：约 63.4 MB 的离线 Zipformer CTC small INT8 作为 Lite 候选，约 228 MB 的 INT8-only SenseVoiceSmall 作为 Standard 候选；禁止生产 Manifest 下载包含约 894 MB FP32 权重的 SenseVoice 完整包。
- ASR-1 验收后转入 ASR-2；Model Manager 按“合同与状态 → 安全下载/安装 → WebUI → 中国源资格验证”顺序交付，并增加制品级再分发审批门禁。
- 修正模型许可证口径：SenseVoice 精确归档适用 FunASR Model License 1.1；Zipformer 精确归档未携带许可证文件，镜像再分发保持待审批。
- 模型再分发口径落定（ASR-2 第三步）：SenseVoice 与 Lite 均保持 `upstream_only`，暂不 ship 任何 `mirror_approved` 源；多源回退机制已就位，待中国源获批后「中国源优先 + 上游回退」无需改安装器。
- ASR-3 自动建议与正式默认批准分离：当前无模型中文建议 Standard、已装 Lite 可完成中文单包体验；Lite 许可证未确认且 ASR-4 基准未完成，因此所有 Tier 的 `formal_default` 仍为 false。

### Fixed

- 修复租约易主后旧 Worker 仍能推进状态的问题：`advance_job` 现校验 `lease_owner` + 租约有效性，失败抛 `LeaseLostError`。
- 修复 `max_attempts` 不生效的问题：领取与查询均约束 `attempt < max_attempts`，耗尽原子转 `failed`（`MAX_ATTEMPTS_EXCEEDED`）。
- 修复恢复扫描「先读后写」竞态：`recover_stale_jobs` 改为带条件的原子 UPDATE。
- 修复 Repository 返回依赖 `expire_on_commit=False` 的问题：写后重新读取，默认 Session 下不再 `MissingGreenlet`。
- 修复 ASR-0 import 守卫扫描范围过宽：改为按架构边界限定（豁免 `asr/providers/`），并新增基础依赖不含引擎库的硬约束守卫。
- 修复 ASR 归一化结果无约束：`ASRSegment`/`ASRResult` 改为 Pydantic，强制非负有限时间、`end >= start`、单调顺序与身份必填。
- 修复 `segment_boundary_error` 除以段数导致放大两倍且静默忽略缺失/幻觉段：改为除以边界数并对未匹配段加惩罚。
- 修复 ASR 归一化结果构造后仍可被改回非法状态：`ASRSegment`/`ASRResult` 冻结（`frozen=True`）并用 tuple 承载 `segments`/`warnings`。
- 修复静音样本幻觉被评满分：CER/WER/边界误差在空 reference 且 hypothesis 非空时返回 1.0。
- 修复 Fake 无法表达零片段结果：改用 `segments is None` 判断，零片段完成时进度报 1.0。
- 修复 import 守卫对动态 import 的能力被过度声称：新增字面量 `importlib.import_module` 扫描，并明确严格隔离依赖 Provider 注册表。
- 修复 Windows 错载 System32 ONNX Runtime 1.10 导致 sherpa C API version 27/10 冲突：导入 sherpa 前固定 ASR 环境 DLL 目录。
- 修复真实 ASR 被 Fake 测试掩盖的四处缺陷：VAD 未 flush、把 `front` 当函数、sample offset 未换算为秒、Recognizer 未调用 `decode_stream()`；音频改为 512-sample 流式 VAD，避免长文件整段常驻内存。
- 修复模型 helper 漏保留 Lite `bbpe.model`，并用测试确保不提取未使用的 FP32 `model.onnx`。
- 修复多源回退「同 SHA ⇒ partial 跨源可复用」的错误推论（P1）：坏源留下的错误前缀会被跨源续传拼接成完整坏文件导致持续失败；现切源即清空 partial + 归零字节从零下载，且 `DigestMismatchError` 也触发回退（不再对坏 primary 直接 failed）。
- 修复卸载一致性（P1，两轮复核）：原子改名到 `.trash/<model_id>/<unique>` → 提交 DB → 删 trash；DB 删除失败时立即恢复原位；启动 `cleanup_trash` 按 DB 记录判断恢复或删除，消除「改名后、DB 删除前崩溃」的孤儿窗口；API 用 `reclaimed_bytes`（实际回收）+ `pending_reclaim_bytes`（待系统清理）区分，前端不再把 pending 数字谎报为「已释放」。
- 修复 `ModelManagerService.close()` 无条件关闭 HTTP client（P2）：仅在服务自建 client（`_owns_client`）时关闭。
- 修复后台安装任务异常静默丢失（P2）：捕获并记录 DB/系统异常，避免 `Task exception was never retrieved`。
