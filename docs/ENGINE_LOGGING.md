# Engine File Logging Contract（F0）

状态：冻结（2026-09-10）。来源：首位试用者反馈 #7（后台启动 stderr 丢失，模型安装失败只能靠猜）。
执行计划见 `docs/FIRST_USER_FEEDBACK_FIX_PLAN.md` §2。

## 1. 目的与范围

后台启动（开机自启/无控制台）的 Engine 提供最小滚动文件日志，让"窗口隐藏时定位一次
模型安装失败与一次 LLM 探测"成为可能。**只加文件 sink，不改任何日志语句的既有纪律**；
STDIO Bridge 永远不启用文件日志（stdout 只承载 MCP 协议）。

## 2. 冻结常量

实现必须与下方逐字一致（漂移测试 `tests/unit/test_engine_logging.py::test_frozen_constants_match_contract` 锁定）：

```python
LOG_DIRNAME = "logs"
LOG_FILENAME = "engine.log"
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_BACKUP_COUNT = 3
```

- 位置：`<data>/logs/engine.log`，`<data>` 即 `resolve_runtime_paths` 的平台数据目录
  （Windows 默认 `%LOCALAPPDATA%\EvoBlue\EvoBlue Video MCP`）。
- 滚动：`logging.handlers.RotatingFileHandler`，单文件 2,000,000 字节，保留 3 份历史，
  总占用上限约 8 MB；编码 UTF-8。
- 格式：`%(asctime)s %(levelname)s %(name)s %(message)s`。

## 3. 接线（唯一调用方 = 引擎入口）

- `__main__.py` 在**单实例检查通过、token 持久化之后**调用 `setup_engine_file_logging`；
  提前退出的路径（退出码 3/4）不写文件日志——第二个实例不得写第一个实例的日志文件。
- setup 把文件 handler 与一个 stderr handler 挂到 root（uvicorn 0.34 的默认 dictConfig
  不含 root 条目，root 上的 handler 不会被 uvicorn 覆盖）；root 的 stderr handler 保证
  应用层记录不因 root 被配置而失去 lastResort 兜底（信息不少于现状）。
- uvicorn 接线经 `log_config` 字典：深拷贝 uvicorn 默认配置后，把**同一个文件 handler
  实例**（工厂返回既有实例，杜绝双实例写同一文件破坏 Windows 轮转改名）追加进
  `uvicorn` 与 `uvicorn.access` 的 handler 列表。uvicorn 自身记录的 stderr 格式与现状
  一致。
- 接入失败的既有日志调用（`asr/service.py`、`storage/rebuild.py` 等 stdlib logging）
  自动进入文件 sink，无需逐处改造。

## 4. 允许与禁止字段

- 允许：阶段、稳定原因码、异常类型、耗时、HTTP 状态、操作 ID、版本号。
- 禁止：请求/响应全文、认证头、Cookie、Key、token、完整凭据引用、含敏感参数的 URL。
- 不得以直接输出异常字符串代替脱敏。

## 5. 脱敏（兜底之网，不是许可）

文件 handler 的 Formatter 对**整段格式化输出（含 traceback）**执行形状脱敏：

- `Authorization: ...`（头名后至行尾）、`Bearer <8+ 字符>`、`sk-<16+ 位键>` → `[REDACTED]`；
- 引擎本机令牌（长度 ≥ 16 的精确串）→ `[REDACTED]`。

形状匹配是尽力而为的网，不是新增日志语句免于脱敏审查的许可；新日志点仍按 §4 自审。

## 6. 降级语义（故障即安全）

- `logs` 目录创建或日志文件打开抛任何 `OSError`：**不启用文件日志，Engine 照常启动**，
  stderr 行为不变，任何界面/导出不得声称日志已落盘。
- 诊断导出（`GET /api/diagnostics/export`）的 `settings.engine_log_file` 只报告**预期位置**
  （`redact_path` 尾两段，如 `.../logs/engine.log`），不代表文件存在。

## 7. Bridge 排除

`evoblue_video_mcp.mcp.__main__` 及其导入链不得触发文件日志；Bridge stdout 纯净由
`tests/integration/test_bridge_stdio.py` 回归，导入无副作用由
`tests/unit/test_engine_logging.py::test_bridge_import_never_activates_file_logging` 回归。

## 8. 用户文档

`docs/SUPPORT.md` 症状表给出日志位置与大小上限；隐私口径注明日志同样脱敏。
