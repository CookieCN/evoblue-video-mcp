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

## 13. 代码许可证不能替代模型权重与转换制品许可证

**Problem**：许可证清单把 SenseVoice 和 Zipformer 权重都直接标成 Apache-2.0，但精确 SenseVoice 归档内的 `LICENSE` 实际指向 FunASR Model License，精确 Zipformer 归档则没有任何许可证文件。

**Root Cause**：把上游代码仓库的许可证、训练数据集许可证和模型权重/转换归档的再分发条款混为一谈；“上游项目是 Apache-2.0”不能自动推出任意转换制品可由 EvoBlue 镜像再分发。

**Solution**：许可证清单改为记录精确制品、归档内证据、上游证据与再分发审批状态；ASR-2 Manifest 区分 `upstream_only`、`mirror_approved` 与 `blocked`，没有使用条款的制品直接拒绝，未获再分发批准的镜像不得列入下载源。

**Rule**：每个生产模型 Manifest 必须有精确制品级许可证证据、署名义务和下载源再分发依据。缺任一项只能本地验证或从官方源下载，不能进入自建镜像。

## 14. 等待外部能力的 Job 不能伪装成失败或运行态

**Problem**：缺 ASR 模型若直接报 `ASR_MODEL_MISSING`，用户只看到失败；若保留在 `transcribing`，Worker 会反复领取并空转，还可能在安装升级后改变既有任务的模型选择。

**Root Cause**：把“当前缺少用户尚未同意安装的能力”当成普通异常，没有区分可恢复等待、自动重试和永久失败，也没有持久化首次路由决策。

**Solution**：增加非 claimable 的 `waiting_for_model` 状态；进入时释放租约并钉住 provider/model/version + 单一推荐模型。Model Manager 只有在用户显式安装成功后才将匹配任务原子恢复到 `transcribing`，Worker 再按已钉路由继续。

**Rule**：等待用户安装、授权或挂载的外部能力必须使用显式持久等待态，释放执行租约、禁止定时空转，并提供由具体外部事件触发的幂等恢复；路由选择需在等待前持久化。

## 15. SAPI 强制非原生采样率会产出饱和垃圾音频

**Problem**：用 `SpFileStream.Format.Type = SAFT16kHz16BitMono` 直接要求 16 kHz 输出，合成结果波形 RMS 恒为 0.996（削波饱和），ASR 模型 CER 高达 0.5–0.9；错误表象是「模型质量差」，实际是语料生成坏了。

**Root Cause**：Desktop 语音（Huihui/Zira）原生 22.05 kHz，强制 SAPI 内部格式转换路径会产生饱和输出；另外不设置 `AudioOutputStream` 时 `Speak` 走扬声器而非文件流。

**Solution**：始终以 `SAFTDefault`（原生格式）合成，再用 numpy 线性插值重采样到目标采样率；`sapi.AudioOutputStream = stream` 必须显式设置。

**Rule**：ASR 基准语料生成后必须先检查波形能量（RMS 分布、峰值、语音/停顿结构）再跑评分；模型得分异常时先怀疑语料与评测链路，再怀疑模型。

## 16. Windows ctypes 调 GetCurrentProcess 必须显式声明 64 位签名

**Problem**：`ctypes.WinDLL("psapi").GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ...)` 静默失败返回 0，峰值内存测量拿不到数据且无异常。

**Root Cause**：GetCurrentProcess 返回 64 位伪句柄（-1），ctypes 默认 restype 按 32 位 int 截断，API 收到被截断的句柄。

**Solution**：`kernel32.GetCurrentProcess.restype = ctypes.c_void_p`，并为 GetProcessMemoryInfo 显式设置 argtypes/restype。

**Rule**：任何返回句柄或指针的 Windows API 在 ctypes 中都必须显式声明 restype/argtypes，不能依赖默认 int 推断。

## 17. PyInstaller 对运行时动态导入的驱动默认不收集

**Problem**：打包后的引擎首启即崩：`ModuleNotFoundError: No module named 'aiosqlite'`——源码环境一切正常。

**Root Cause**：SQLAlchemy 的 sqlite+aiosqlite 方言在 `create_async_engine` 时才字符串导入驱动，静态分析看不到；同类风险还有 uvicorn 的 loops/protocols 自动选择、keyring 平台后端。

**Rule**：打包矩阵必须包含「干净数据目录首启」冒烟；spec 文件为每个运行时字符串导入点显式声明 hiddenimports（aiosqlite/sqlalchemy.dialects.sqlite/uvicorn.*/keyring.backends.*）。

## 18. 门禁阈值冻结必须与 harness 调试期隔离，失败要留痕

**Problem**：ASR-4 门禁前两轮结果（Standard CER 0.57）全部作废——是语料生成和 WER 归一化的 bug，不是模型差异；如果直接按作废轮次调阈值，会把 harness 缺陷冻进门禁。

**Root Cause**：评测链路本身没有验证环节，坏语料产出的「差分」与真实模型差分无法从单次报告区分。

**Solution**：先用已知可转写的真实音频（lei-jun-test.wav）做链路对照，波形能量检查定位语料问题；WER 改用保留词边界的 `normalize_words`，CER 保持去空白归一化。最终阈值只在修复后的链路上冻结一次。

**Rule**：基准门禁文档必须记录被丢弃的运行及原因（harness-validation trail）；门禁对照真实音频样本先验证评测链路，再信任模型得分。

## 19. 读用户级 MCP 配置会把明文凭据带进 AI 会话，进入会话即视为已暴露

**Problem**：Codex 验收会话在诊断中读取用户级客户端配置（`~/.codex/config.toml` 的 `http_headers`、`~/.workbuddy/mcp.json` 的 `env`/`headers`），AgentKey 与 SellerSprite 明文凭据原样进入会话输出；同日本项目会话整读 `~/.workbuddy/mcp.json` 也把 Semrush/SellerSprite key 显示进了工具结果。这类配置格式本来就要求凭据内联（条目无外部引用约定），而「读文件 → 回显 → 送模型」链路会把被读内容送出本机。

**Root Cause**：把「读取配置文件验证结构」当成无敏感操作；实际上任何整文件原文进入上下文的动作都会把内联凭据交给模型服务商。

**Solution**：当日轮换全部涉事凭据；后续读取此类文件只做字段级提取（key 名、command/args 形态、enabled 状态），用 JSON/TOML 解析取结构而非整文件回显；无法避免时在回复中不复述值并明确提示轮换。

**Rule**：凡进入 AI 会话（工具结果、日志、粘贴报告均算）的凭据一律视为已暴露，立即轮换；读用户级配置优先解析取字段，不整文件 cat 进上下文；跨 agent 交接文档只引用凭据文件位置，不引用内容。

## 20. 测试不许触到真实外部系统——注入缝必须在服务构造函数上

**Problem**：P5 集成测试跑 `claude mcp add` 时，本机 PATH 上真有 `claude` CLI，测试把 `evoblue-video` 真实写进了 Wilson 的 Claude Code 配置。巧合是配置内容恰好正确（路径、参数都对），但这是测试未经授权修改用户真实状态。

**Root Cause**：`ClientConfigService` 内部 `CliRunner()` 硬编码生产实现，测试没有注入缝，只能打到真 CLI。

**Solution**：服务构造函数加 `cli_runner` 参数（生产默认、测试传 fake）。规则：**任何会 spawn 子进程、写用户文件、发网络请求的依赖，注入缝必须在最外层构造函数上**；测试环境断言 fake 成本远低于事故成本。验收脚本触真实系统前先做独立字节快照（sha256 前后比对），跑完恢复或明确报告保留理由。

## 21. 路径大小写差异是「受管修复」不是 no-op——还原用户状态要靠快照不是靠记忆

**Problem**：P5 真机验收把 Codex/WorkBuddy 条目里 P4 时代的小写 `f:\` 盘符静默规整成 `F:\`（受管字段差异→替换），WorkBuddy 还连带丢了冗余 `disabled:false`。功能等价，但 WorkBuddy 的 Trust 状态可能与条目内容挂钩。

**Root Cause**：Windows 路径大小写不敏感是语义事实，但字符串比较是大小写敏感的——「功能等价」与「字节相同」是两回事；no-op 判据用哪一个取决于产品语义。

**Solution**：验收/测试前用独立快照（cp + sha256）留底，结束后恢复字节或说明保留理由；「工具会做什么」写进合同（本案：受管修复是设计行为，已在合同 §10 记录）。若要避免这类重写，比较层需要平台感知的路径归一化——未做，留给 Wilson 决策。

## 22. heredoc/工具传输会吞转义序列——不可见字符一律用 chr(92) 构造并回读验证

**Problem**：往 merge_json.py 写 BOM 检查行时，`"﻿"` 经 heredoc 传输变成不可见 BOM 字面量本身，连续两次替换「成功」（脚本无条件打印 done）实际没生效。

**Root Cause**：工具传输层把 `\u` 解了一次转义；且脚本打印 done 不校验结果，假成功。这是 AGENTS.md Gotcha #7 的变体：不只 MSYS 改写路径，任何跨工具的文本传输都会动转义和不可见字符。

**Solution**：需要写转义序列/不可见字符时用 `chr(92)` 之类的构造拼接，写完用 repr 或逐字节 grep 验证；批量替换脚本必须带断言（替换计数、内容校验），绝不无条件打印成功。

## 23. RTL 测试不自动 cleanup——DOM 累积造成「重复元素」假象

**Problem**：Vitest+RTL 下第二个用例 `getByRole` 报 Found multiple elements，排查半天组件逻辑，实际是上一个用例的 DOM 没卸载。

**Root Cause**：RTL 自动 cleanup 依赖测试框架 globals；本项目 vitest 未开 `globals: true`，cleanup 从未执行。既往用例碰巧没有同名元素才没炸。

**Solution**：测试文件显式 `afterEach(cleanup)`；mock fetch 按 URL 路由时用「在 URL 中出现位置最靠后」的键匹配（前缀 `/api/x` 会比后缀 `/x/y` 更长，按长度选会选错）。

## 24. 可复现断言只锁稳定字段——内存/时序指标用存在性+上限门禁

**Problem**：ASR 基准测试断言两次运行的报告逐字节相等，`peak_rss_delta_bytes` 受分配器/缓存/运行顺序影响天然波动，全量套件时绿时红（负载相关，单跑无法复现）。

**Root Cause**：把「确定性指标」和「测量指标」混在同一个相等断言里；RSS 的逐次完全相等没有产品意义。

**Solution**（2026-09-07 评审定调）：精确相等只用于稳定字段；测量类指标断言「存在 + 有限 + 非负」或上限门禁，不删指标本身。新增强制锁：`verified` 状态必须同时有配置证据（contract §6），缓存的 verified 在配置证据消失/不匹配/不可解析时立即失效。
