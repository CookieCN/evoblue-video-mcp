# Job State Machine

## 状态

```text
queued
  -> fetching_metadata
  -> fetching_subtitles
       -> downloading_audio       (无可用字幕且 ASR 允许/必需)
       -> cleaning_transcript     (已有字幕)
  -> transcribing                (音频路径)
       -> waiting_for_model      (未安装：释放租约，等待显式安装)
            -> transcribing      (匹配模型安装成功)
  -> cleaning_transcript
  -> chunking
  -> summarizing_chunks
  -> generating_report
  -> indexing
  -> completed
```

终止状态：`completed`、`failed`、`cancelled`。等待恢复状态：定时重试的 `retry_wait` 与外部事件唤醒的 `waiting_for_model`；后者不可被 Worker 定时领取。

## 状态记录

每个 Job 至少记录：`job_id`、规范化输入、配置指纹、status、stage、progress、attempt、max_attempts、lease_owner、lease_expires_at、cancel_requested_at、稳定 error 字段、阶段产物引用和时间戳。进入 ASR 后还记录已钉住的 provider/model/version 与单一安装建议。

### 身份投影与字幕归因（迁移 v9，F3 2026-09-11）

- `title`/`platform`：元数据阶段成功时一次性写入任务行（列表与状态在等待/失败态即可辨识任务）；
  完整元数据的单一事实来源仍是 `video_metadata` 产物，这两列是单向投影，读取端永不回写。
  元数据未到达时读取端用规范化 `url` 作回退辨识。
- `subtitle_probe`（字幕阶段观测，枚举冻结）：`found`（已获取可用字幕）/ `none`（查询成功但无
  可用字幕——是关于该视频/会话的事实，不是「字幕不可能存在」；部分平台字幕仅对登录 Cookie
  浏览器可见）/ `unavailable`（字幕请求或解析失败，真实原因在 `error_code`/`error_detail`，
  不硬造分类）。写入点：`FetchingSubtitlesHandler` 三个分支。
- `asr=disabled` 且无字幕：`ASR_DISABLED` 的 `error_detail` 附条件性 Cookie 提示。
- 阻塞原因（读取端派生，不落库）：`queued`/`waiting_for_model` 的详情响应附
  `blocked_reason`/`blocked_message`（见 MCP_TOOLS §2；与 Worker 领取门禁同源）。

## 转换规则

- 任何副作用前先持久化目标阶段和执行意图；成功后原子提交产物引用与下一状态。
- 每个阶段按 `job_id + stage + input fingerprint` 幂等；发现有效产物则复用。
- 暂时错误进入 `retry_wait`，保存 `next_retry_at`；超过上限进入 `failed`。
- 缺模型进入 `waiting_for_model` 并释放租约，不增加自动重试、不触发下载；Model Manager 仅在用户同意且匹配模型安装成功后原子恢复到 `transcribing`。
- Engine 重启时，租约过期的运行状态由恢复器检查产物后重入当前阶段或安全回退。
- 进度只单调增加；重试可保持进度但不得伪装为完成。
- 同一 URL、模式和配置指纹可复用近期完成/运行任务；复用窗口由用户配置。

## 取消

- `queued`/`retry_wait`/`waiting_for_model` 可直接原子转为 `cancelled`。
- 运行阶段写 `cancel_requested_at`；Worker 在下载块、ASR 分段、LLM 请求之间的安全点检查。
- 取消意图不得被停靠吞掉（F4 2026-09-11，反馈 #15）：转入 `waiting_for_model` 的提交事务内若存在未消费的 `cancel_requested_at`，原子落为 `cancelled`——等待态是长期驻留状态，吞掉取消会让任务"复活后才取消"，用户视角即"无人取消却变 cancelled"。
- 启动对账清扫悬挂取消：`waiting_for_model` 且带 `cancel_requested_at` 的行（只可能来自修复前版本或异常路径）落为 `cancelled`。
- 用户明确取消的任务不得复活：`resume_waiting_jobs` 只恢复无取消标记的等待行。
- `generating_report` 原子替换开始后不中断文件提交，之后取消索引或标记完成，以避免半份报告。
- `completed`、`failed`、`cancelled` 不可再次取消，返回幂等结果。

## 产物规则

- 临时文件与最终报告使用不同目录/扩展名。
- Markdown 在目标目录创建临时文件，fsync/校验后原子替换。
- 默认不覆盖内容哈希与上次生成哈希不一致的用户文件；产生冲突副本并提示。
- 索引失败不得丢失报告：Job 可在 `indexing` 重试并保留已生成报告。

## 稳定错误码初表

`INVALID_URL`、`UNSUPPORTED_PLATFORM`、`METADATA_FETCH_FAILED`、`SUBTITLE_UNAVAILABLE`、`ASR_DISABLED`、`DOWNLOAD_LIMIT_EXCEEDED`、`DISK_SPACE_LOW`、`FFMPEG_MISSING`、`ASR_MODEL_MISSING`、`ASR_LANGUAGE_UNSUPPORTED`、`LLM_NOT_CONFIGURED`、`LLM_AUTH_FAILED`、`LLM_RATE_LIMITED`、`REPORT_CONFLICT`、`INDEX_FAILED`、`ENGINE_RESTARTED`、`MAX_ATTEMPTS_EXCEEDED`、`CANCELLED_BY_USER`、`INTERNAL_ERROR`。
