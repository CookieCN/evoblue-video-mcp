# Product Requirements Document

## 产品定义

EvoBlue Video MCP 是 local-first 视频理解与知识管理工具。用户输入 YouTube/Bilibili 视频链接，本机 Engine 获取元数据和字幕，无字幕时可选本地 ASR，随后调用用户配置的 LLM，生成标准 Markdown 并建立本地历史与全文搜索。

## 用户与核心任务

- 内容创作者：把长视频快速变成摘要、选题与营销洞察。
- 跨境营销人员：沉淀竞品、受众和评论风向资料。
- AI 客户端用户：从 Codex、Claude Desktop、DeepSeek Harness、WorkBuddy 异步发起与读取分析。

成功不是“工具返回一段摘要”，而是任务可恢复、报告可迁移、配置对普通用户可理解、数据库损坏后知识资产仍可找回。

## 产品原则

Local-first；WebUI 是控制中心；MCP 不拥有状态；长任务异步；Markdown 属于用户；遥测默认关闭；不预测工期。

## 首版非目标

远程服务器、SaaS、注册登录、积分、支付、管理后台、多租户、MarketRadar、SellerSprite、Google Docs/飞书/腾讯文档 API、在线知识库双向同步、云同步、远程 MCP、自动部署与自动 git push。

## 迁移候选

可在新合同和测试之后研究旧 EvoBlue 的 platform detector、yt-dlp、subtitle、audio、whisper、transcript cleaner、chunking、LLM client、video prompts、coverage checker。禁止整体复制用户 API、认证、积分、支付、Admin、MarketRadar、SellerSprite、Share、旧数据库模型和线上域名逻辑。

## 阶段

### P0：Harness、边界、合同、最小骨架

- 目标：让后续 Agent 在确定边界、状态与验证反馈下工作。
- 前置依赖：无。
- 功能范围：Harness 六件套、治理文档、系统/MCP/Job/Markdown/WebUI 合同、Python/前端/CI 最小骨架。
- 非目标：Pipeline、真实 MCP server、数据库、常驻进程、完整 UI。
- 验收标准：合同覆盖失败/幂等/安全；Python 包可导入；健康端点与 Schema 测试通过；JSON 合法；静态检查可运行或明确环境缺失。
- 风险：过早锁死未知 SDK；用合同隔离业务 Schema，P4 再接当前 SDK。
- 回滚：删除未被后续实现依赖的骨架文件；保留 Harness 与 ADR 作为决策记录。

### P1：Local Engine、SQLite、Worker、WebUI 框架

- 目标：形成可启动、持久、可恢复的本地任务底座。
- 前置依赖：P0。
- 功能范围：SQLite 模型/迁移、单 Worker Owner、租约恢复、设置状态、API、WebUI 路由与任务观察。
- 非目标：真实视频平台、LLM、ASR、客户端自动配置。
- 验收标准：提交模拟 Job 后重启可恢复；重复提交幂等；Engine 只绑定 loopback；WebUI 能显示真实状态。
- 风险：单实例锁与 Windows 生命周期差异。
- 回滚：停用自启动与 Worker，保留数据库并回退到只读诊断模式。

### P2：YouTube/Bilibili 字幕分析闭环

- 目标：对有字幕视频生成完整 Markdown 报告。
- 前置依赖：P1。
- 功能范围：平台识别、yt-dlp 元数据/字幕、清洗、分块、LLM、报告；稳定错误码。
- 非目标：本地 ASR、客户端配置、云同步。
- 验收标准：两平台代表样本成功；无字幕/限流/Cookie/超长视频失败可诊断；阶段可重试。
- 风险：平台变更、Cookie 隐私、字幕格式差异。
- 回滚：按平台关闭适配器，不影响历史和其他平台。

### P3：历史记录、Markdown、FTS5 和索引重建

- 目标：把报告变成可迁移、可恢复的知识资产。
- 前置依赖：P2。
- 功能范围：文件模板、冲突保护、历史、FTS5、扫描 Markdown 重建。
- 非目标：在线知识库同步。
- 验收标准：删除 SQLite 后从 Markdown 恢复元数据与搜索；用户修改不被静默覆盖。
- 风险：Schema 演进和损坏 Frontmatter。
- 回滚：关闭索引写入，仍以文件浏览方式提供报告。

### P4：STDIO MCP Bridge 和四客户端

- 目标：七个 MCP 工具跨客户端稳定调用同一 Engine。
- 前置依赖：P1-P3。
- 功能范围：MCP SDK v2 接入、stdio、版本握手、七工具、Codex/Claude/DeepSeek/WorkBuddy 兼容测试。
- 非目标：WebUI 自动写客户端配置。
- 验收标准：真实握手；stdout 无日志；提交快速返回；多客户端不重复执行；长字幕按 section 控制。
- 风险：客户端超时和 Schema 支持差异。
- 回滚：降级为 WebUI 使用，Bridge 独立禁用。

### P5：WebUI MCP 自动配置与真实握手

- 目标：普通用户从 WebUI 安装、验证、移除并恢复客户端配置。
- 前置依赖：P4。
- 功能范围：结构化合并、备份、真实握手、恢复；不确定客户端只生成可复制配置。
- 非目标：猜测未知路径、覆盖其他 MCP 配置。
- 验收标准：保留用户已有配置；失败可恢复；UI 不虚报成功。
- 风险：客户端配置格式变化。
- 回滚：恢复自动备份并切换到手动复制引导。

### P6：Faster-Whisper、模型管理和恢复

- 目标：无字幕视频可选择本地 ASR。
- 前置依赖：P2-P3。
- 功能范围：音频、FFmpeg、模型/设备、转录阶段、磁盘与恢复。
- 非目标：云 ASR。
- 验收标准：CPU 基线成功；可诊断 GPU/CTranslate2；模型中断可恢复；限制生效。
- 风险：模型体积、GPU 驱动、打包兼容。
- 回滚：禁用 ASR 并保留字幕分析能力。

### P7：Windows 安装器和 GitHub Release

- 目标：非开发者可安装、升级、卸载。
- 前置依赖：P1-P6 稳定合同。
- 功能范围：PyInstaller onedir、依赖/许可证、用户级自启动、迁移、Release。
- 非目标：远程部署、自动 git push。
- 验收标准：干净 Windows 测试机完成安装/升级/卸载，用户数据默认保留且可选择清除。
- 风险：杀软误报、FFmpeg/模型体积。
- 回滚：保留上一发行包和数据迁移回退路径。

### P8：公开测试和 Harness 加固

- 目标：把真实失败沉淀为反馈和治理护栏。
- 前置依赖：P7。
- 功能范围：公开测试反馈、eval、故障注入、兼容矩阵、审计、支持文档。
- 非目标：用人工流程替代可自动化验证。
- 验收标准：关键失败模式有测试、错误提示和恢复路径；回归进入 CI。
- 风险：样本偏差与平台快速变化。
- 回滚：按功能旗标关闭不稳定适配器，保留核心资产访问。

