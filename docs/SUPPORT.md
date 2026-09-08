# Support / 疑难排解

面向使用者的症状 → 原因 → 处置表。深入的技术细节见 `docs/` 下对应合同；
反馈 bug 时请先在 WebUI「设置 → 导出诊断信息」下载脱敏诊断包并附在 issue 里
（文件不含绝对路径、API Key 或 Cookie）。

## 常见症状

| 症状 | 原因 | 处置 |
|---|---|---|
| 双击 setup.exe 被 Windows SmartScreen 拦截（「已保护你的电脑」） | 安装器未做代码签名（开源项目常见） | 点「更多信息」→「仍要运行」。发布页的 `SHA256SUMS.txt` 可核对安装包哈希 |
| 杀毒软件报「含风险程序」并隔离引擎 exe | 未签名 PyInstaller 程序的常见误报 | 在杀软中恢复并加白名单 `evoblue-engine-full.exe`；核对 SHA256 后仍报毒请提 issue |
| 启动后窗口一闪而过 / 无浏览器弹出 | 端口被占用，或自动打开浏览器被禁用 | 看命令行 stderr：退出码 3 = 已有实例在运行（直接用已运行实例）；退出码 4 = 端口被占，设置环境变量 `EVOBLUE_OPEN_UI=0` 可关自动开浏览器 |
| WebUI 打开要求「粘贴本机访问令牌」 | 浏览器没有本机令牌（清过浏览器数据 / 换了浏览器 / 手动输入地址） | 打开数据目录（默认 `%LOCALAPPDATA%\EvoBlue\EvoBlue Video MCP`），用记事本打开 `local_token` 文件，全选复制粘贴进门页 |
| 首页提示「有任务在排队但不会开始」 | 初始设置未完成（LLM 配置或 Key 缺失） | 到「设置」页完成初始设置；完成后排队任务自动开始 |
| 提交 YouTube 链接后任务失败，错误码 `SUBTITLE_UNAVAILABLE` | 该视频没有可用字幕 | 到「模型」页安装本地语音模型走语音转写（需要 FFmpeg，见下一条） |
| 诊断里 FFmpeg 显示「未找到」 | 未安装 FFmpeg（字幕模式不需要它；只有本地语音转写需要） | `winget install Gyan.FFmpeg` 或从 ffmpeg.org 下载后加入 PATH |
| 模型下载很慢 / 中断后重来 | 中国大陆到上游源的网络波动 | 下载支持断点续传：重试同一模型会从断点继续；不要反复取消 |
| 搜索 / 历史为空但报告文件还在 | 索引与数据库不同步 | WebUI 触发「重建索引」，或重启 Engine（启动时自动对账） |
| MCP 客户端里工具报 `ENGINE_NOT_READY` | EvoBlue Engine 没在运行 | 启动 EvoBlue（安装时自启动已默认开启）；Bridge 不会自动拉起 Engine（设计如此，ADR 0004） |
| MCP 客户端里工具报 `ENGINE_UNAUTHORIZED` | 客户端拿到的令牌与 Engine 不一致 | 在 WebUI「客户端」页重新「安装配置」；令牌经数据目录自动发现，无需手填 |
| 升级后想回退到旧版本 | 升级迁移不可逆（forward-only） | 升级时引擎自动生成 `evoblue.db.bak-v<旧版本>`；恢复备份 + 装回旧安装包，见 `docs/MIGRATION_ROLLBACK.md` |
| 想彻底清除个人数据 | — | 卸载时在确认框选「是」（默认保留）；或手动删除数据目录 |

## 反馈渠道

- Bug：GitHub Issues 的 bug 模板（附「导出诊断信息」的 JSON 文件）
- 功能建议：feature 模板
- 安全问题：不要开公开 issue，按 `SECURITY.md` 的私密渠道报告

## 隐私口径

- 全部数据留在本机；无遥测（`telemetry_enabled` 默认关闭）
- 诊断导出已脱敏：路径只保留尾部两段、Key/Cookie 只显示已配置/未配置
- 日志不回显凭据；Engine 只绑定 127.0.0.1
