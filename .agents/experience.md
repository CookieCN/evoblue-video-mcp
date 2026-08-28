# Experience Log

> 记录每次踩坑的完整链路：Problem -> Root Cause -> Solution -> Rule。
> 格式：`## N. 标题（一句话说清问题）`，然后记录 **Problem** / **Root Cause** / **Solution** / **Rule**。

## 1. 沙箱账户触发 Git dubious ownership

**Problem**：在仓库内执行 `git status` 被 Git 拒绝。

**Root Cause**：仓库属于桌面用户，执行命令的是受限沙箱账户。

**Solution**：对只读 Git 命令使用单次 `git -c safe.directory='<repo>' ...`。

**Rule**：不得为了消除该提示修改用户全局 Git 配置；只在必要命令内局部放行。
## 2. 用户 npm registry 与执行网络均不可达

**Problem**：`npm install` 长时间无输出，前端依赖无法安装。

**Root Cause**：用户 npm 配置指向 `registry.npm.taobao.org` 且连接被拒；单次切换官方 `registry.npmjs.org` 后 `npm ping` 同样 `ECONNREFUSED`，确认是当前执行环境网络而非依赖声明。

**Solution**：不修改全局 npm 配置；终止有限重试，保留 JSON 校验，将前端 lint/test/build 标记为 `SKIP_ENVIRONMENT`。

**Rule**：依赖安装无输出时用显式 registry 和 `npm ping --loglevel verbose` 区分用户配置、网络与项目依赖问题；重试必须有限。
## 3. mypy strict 拒绝 SQLAlchemy 2.0 async 的 rowcount

**Problem**：`session.execute(update(...))` 后访问 `.rowcount` 报 `Result[Any] has no attribute "rowcount"`。

**Root Cause**：SQLAlchemy 2.0 async 的 `execute` 对 DML 语句类型推断为 `Result[Any]`，而 `rowcount` 只在 `CursorResult` 上，strict 模式因此拒绝。

**Solution**：乐观锁 UPDATE 改用 `.returning(Job.id)` + `scalar_one_or_none()` 判断是否命中，类型干净且能区分「未命中」与「命中」；SQLite 3.35+ 支持 UPDATE…RETURNING。

**Rule**：用乐观锁 UPDATE 判断抢占是否成功时，优先 `.returning(主键)` + `scalar_one_or_none()`，不要依赖 `rowcount`。
## 4. 租约推进与恢复必须原子 CAS，不能先读后写

**Problem**：审查发现 `advance_job` 未校验租约、`recover_stale_jobs` 先 SELECT 后无条件 UPDATE，导致旧 Worker 能推进状态、恢复器覆盖新 Worker 的租约。

**Root Cause**：把「状态/租约」当成可先读后改的普通字段，忽略了 SELECT 与 UPDATE 之间的并发窗口；ORM 的 dirty-check 提交按主键无条件覆盖。

**Solution**：`advance_job` 用单条 `UPDATE ... WHERE lease_owner=:owner AND lease_expires_at > :now AND status=:old` 做 CAS，行数 0 即拒绝；`recover_stale_jobs` 用带 `lease_expires_at <= :now` 条件的原子 UPDATE 并返回受影响 id。

**Rule**：凡涉及所有权（租约、锁、抢占）的状态推进与清理，必须单条带条件的 UPDATE/CAS 完成，禁止「先 SELECT 后按主键写」。

## 5. Windows sandbox 目录 Owner 异常会让所有命令在启动前失败

**Problem**：PowerShell、cmd、`apply_patch` 均在进程创建前失败，统一报 `helper_unknown_error: setup refresh had errors`；重启和 Windows“修复应用”无效。

**Root Cause**：`~/.codex/.sandbox/sandbox.2026-08-25.log` 明确记录 `SetNamedSecurityInfoW failed: 5`，目标为项目 `.agents`。该目录 Owner 被错误改成 `DESKTOP-QQ22PEB\CodexSandboxOffline`，sandbox helper 无权更新所需 ACE。

**Solution**：只对日志点名的 `.agents` 恢复 Owner 为项目所有者 `DESKTOP-QQ22PEB\w`；目录内文件原本已属于该用户。恢复后普通 workspace-write 命令立即成功。

**Rule**：遇到 `setup refresh had errors`，先读当天 sandbox 日志定位具体路径与错误码，再比较 Owner/ACL。禁止反复使用 Full Access 或全面放宽磁盘权限；仅修复日志点名的异常目录，并在修复后立即用普通 sandbox 命令复测。

## 6. 合同/守卫/基准指标的三处边界必须精确，测试不能固化错误口径

**Problem**：ASR-0 首版被 review 指出三处缺陷——import 守卫扫描整个 `src` 会阻断未来合法的 `asr/providers` 引擎适配器；`segment_boundary_error` 除以段数导致放大两倍且 `zip` 静默忽略缺失/幻觉段；Fake 完全忽略取消/进度回调使行为合同形同虚设。更糟的是我写的 metrics 单测把「除以段数」的错误期望 `0.3` 固化成了断言。

**Root Cause**：把「测试通过」当成「合同正确」；守卫范围、指标分母、行为语义这些边界没有被精确建模，测试反而把错误实现锁死。

**Solution**：守卫按架构边界限定（静态扫描豁免 `asr/providers/`，另加 pyproject 基础依赖不含引擎库的硬约束）；指标改为除以边界数 `2n` 并对缺失/幻觉段加惩罚；归一化结果改用 Pydantic 强制非负有限时间、`end >= start`、单调、身份必填；Fake 真正执行取消/进度语义，并由反例测试验证「不该通过但会被静默接受」的输入。

**Rule**：写守卫/指标/行为合同前先定义边界与分母口径；反例测试必须覆盖「会被静默接受」的输入，而不是只测 happy path；不要把「全绿」等同于「合同完整」。

## 7. Pydantic 校验只保护构造，运行期不可变要靠 frozen + 不可变集合

**Problem**：ASR 归一化结果用 Pydantic 加了字段约束，但构造后 `result.segments.reverse()` 和 `result.segments[0].start = -9` 仍能把它改回非法状态；且 `segments or default` 把合法的空列表当缺省，静音/零片段路径无法表达。

**Root Cause**：Pydantic 的 validator 只在构造时运行；默认 BaseModel 可变、`list` 字段可变。用 `x or default` 会误伤 falsy 的合法值（空 list/str）。

**Solution**：合同对象用 `ConfigDict(frozen=True)` 且集合字段用 `tuple`，运行期赋值与原地修改都被拒绝；缺省值判断用 `x is None` 而非 `x or ...`，区分「未提供」与「提供了空值」。

**Rule**：凡要跨边界传递、会被下游缓存/写盘的合同对象，一律 frozen + 不可变集合，别只靠构造期 validator；缺省值用 `is None`，别用真值判断吞掉合法的空值。

## 8. 模型文件大小必须区分发布包、选用权重与安装占用

**Problem**：SenseVoice 规划按 INT8 权重约 229 MB 估算，但执行 Agent 下载后观察到制品约 1.05 GB，导致误以为 INT8 方案体积失控。

**Root Cause**：使用了同时包含约 894 MB FP32 `model.onnx` 和约 228 MB INT8 `model.int8.onnx` 的完整发布包，而不是官方 INT8-only 制品；规划数字也没有明确区分网络下载体积、归档解压体积和最终选用文件体积。

**Solution**：生产 Manifest 只允许固定 INT8-only 制品；分别记录并展示实测 `compressed_size_bytes` 与 `installed_size_bytes`，安装后校验允许文件清单，拒绝或清理未声明的 FP32 权重。中文首体验增加更小的 Zipformer CTC small INT8 候选，但仍受质量与许可证门禁约束。

**Rule**：模型体积不得引用参数量或归档目录中的单个文件作下载承诺；发布前必须对每个精确 URL 实测下载字节、解压后允许文件总量和 SHA-256，并用测试阻止“同包多精度权重”进入安装目录。

## 9. ONNX Runtime 的 version 报错先查 C API DLL，不要先怪模型

**Problem**：sherpa-onnx 1.13.6 加载两个有效模型都退出并报告 `The given version [27] is not supported, only version 1 to 10`，一度被判断为 wheel 静态捆绑的运行时过旧，只能等待上游或源码编译。

**Root Cause**：`27` 是 sherpa 扩展请求的 ONNX Runtime C API 版本，不是 ONNX 模型版本。Windows DLL 搜索实际命中了 `C:\Windows\System32\onnxruntime.dll` 1.10；sherpa wheel 的 `.pyd` 动态依赖运行时，并非静态捆绑。独立 Python `onnxruntime` 能加载模型只证明模型有效，不能证明 sherpa 进程绑定了同一 DLL。

**Solution**：Windows ASR extra 显式依赖 `onnxruntime>=1.27`，Provider 在 import sherpa 前通过 `os.add_dll_directory()` 注册该包的 `capi` 目录并保留 handle。最小复现由失败变为 `LOADED OK`，随后 Lite/Standard 均完成真实音频转写。

**Rule**：遇到 `The given version [N] ... only version 1 to M`，先按 C API ABI/DLL 冲突调查实际加载路径；必须在同一进程、同一加载顺序下做最小复现。真实引擎未跑通时不得用 Fake/代码层全绿把 proof-of-capability 标完成。

## 10. 真实 ASR 必须验证“检测—解码—时间轴”整条链

**Problem**：解除 DLL 阻塞后，真实有声 WAV 仍返回零段；进一步执行后又出现 `front` 调用错误和空文本，而 194 项既有测试全部通过。

**Root Cause**：实现遗漏 `vad.flush()` 与 `recognizer.decode_stream()`，误把 pybind 的 `front` 属性当方法，并把以 sample 为单位的 VAD start 直接当秒；Fake Provider 无法暴露任何一个第三方 API 语义错误。

**Solution**：按官方 Silero VAD 口径以 512 samples 流式喂入，文件末尾 flush，读取 `vad.front`，将 sample offset 除以采样率，并显式 decode 后读取 result；新增 Lite/Standard 真实模型测试和模型提取反例测试。

**Rule**：ASR proof-of-capability 至少要用真实模型覆盖非静音音频、静音音频、两档模型、非空文本、有界单调时间戳；Fake 只验证合同与故障路径，不能作为第三方 API 接线验收。

## 11. 两个同名 `validate_transition` import 会静默互相覆盖

**Problem**：repository 同时 import 了 `jobs.transitions.validate_transition`（Job 状态机）和 `storage.model_download.validate_transition`（下载状态机），后者覆盖前者，导致 Job 状态机调用到下载状态机的字典，19 个既有测试报 `KeyError: <JobStatus.INDEXING>`。

**Root Cause**：两个模块导出了同名函数 `validate_transition`，Python 后 import 覆盖前 import；mypy 同时报 incompatible import，是这种冲突的明确信号。

**Solution**：给后 import 起别名（`validate_transition as validate_download_transition`），下载操作处用别名调用；让 mypy 先红后绿来确认命名空间恢复。

**Rule**：import 同名符号（尤其 `validate_*`/`apply_*` 这类通用名）必须用别名区分，不能依赖「后 import 覆盖前 import」；新增状态机时先检查是否与现有 `validate_transition` 撞名。

## 12. 已发布的迁移版本绝不能重写其 DDL

**Problem**：ASR-2 先把 v5 定义成 `model_install_state` 单表，下一轮为拆三表直接改写了 v5 的 DDL。任何已执行旧 v5 的数据库（schema_migrations 记录 version=5）启动时会跳过新 DDL，三表根本不会建。

**Root Cause**：把「本轮刚加、还没真实用户」等同于「可以改」，忽略了 migrations 的「冻结历史」原则——迁移的幂等性依赖「已 applied 的版本不再重跑」，重写已发布版本的 DDL 就破坏了这条不变式。

**Solution**：恢复 v5 为原单表 DDL，新增 v6 做 `DROP model_install_state` + 建三表，并加「冻结旧 v5 库 → 升级 v6」的回归测试（手动建 model_install_state + schema_migrations 记录 1-5，init_db 后断言旧表 drop、三表存在）。

**Rule**：迁移一旦写入 `_MIGRATIONS` 并可能被任何环境执行，其 DDL 就是不可变的；schema 变更只能追加新版本 + 显式 drop/rebuild，绝不能改历史版本的 SQL。加迁移必配「冻结旧库升级」测试。

## 11. 代码许可证不能替代模型权重与转换制品许可证

**Problem**：许可证清单把 SenseVoice 和 Zipformer 权重都直接标成 Apache-2.0，但精确 SenseVoice 归档内的 `LICENSE` 实际指向 FunASR Model License，精确 Zipformer 归档则没有任何许可证文件。

**Root Cause**：把上游代码仓库的许可证、训练数据集许可证和模型权重/转换归档的再分发条款混为一谈；“上游项目是 Apache-2.0”不能自动推出任意转换制品可由 EvoBlue 镜像再分发。

**Solution**：许可证清单改为记录精确制品、归档内证据、上游证据与再分发审批状态；ASR-2 Manifest 区分 `upstream_only`、`mirror_approved` 与 `blocked`，没有使用条款的制品直接拒绝，未获再分发批准的镜像不得列入下载源。

**Rule**：每个生产模型 Manifest 必须有精确制品级许可证证据、署名义务和下载源再分发依据。缺任一项只能本地验证或从官方源下载，不能进入自建镜像。

## 14. 等待外部能力的 Job 不能伪装成失败或运行态

**Problem**：缺 ASR 模型若直接报 `ASR_MODEL_MISSING`，用户只看到失败；若保留在 `transcribing`，Worker 会反复领取并空转，还可能在安装升级后改变既有任务的模型选择。

**Root Cause**：把“当前缺少用户尚未同意安装的能力”当成普通异常，没有区分可恢复等待、自动重试和永久失败，也没有持久化首次路由决策。

**Solution**：增加非 claimable 的 `waiting_for_model` 状态；进入时释放租约并钉住 provider/model/version + 单一推荐模型。Model Manager 只有在用户显式安装成功后才将匹配任务原子恢复到 `transcribing`，Worker 再按已钉路由继续。

**Rule**：等待用户安装、授权或挂载的外部能力必须使用显式持久等待态，释放执行租约、禁止定时空转，并提供由具体外部事件触发的幂等恢复；路由选择需在等待前持久化。
