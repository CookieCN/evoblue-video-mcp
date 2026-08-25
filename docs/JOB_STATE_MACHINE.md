# Job State Machine

## 状态

```text
queued
  -> fetching_metadata
  -> fetching_subtitles
       -> downloading_audio       (无可用字幕且 ASR 允许/必需)
       -> cleaning_transcript     (已有字幕)
  -> transcribing                (音频路径)
  -> cleaning_transcript
  -> chunking
  -> summarizing_chunks
  -> generating_report
  -> indexing
  -> completed
```

终止状态：`completed`、`failed`、`cancelled`。等待恢复状态：`retry_wait`。

## 状态记录

每个 Job 至少记录：`job_id`、规范化输入、配置指纹、status、stage、progress、attempt、max_attempts、lease_owner、lease_expires_at、cancel_requested_at、稳定 error 字段、阶段产物引用和时间戳。

## 转换规则

- 任何副作用前先持久化目标阶段和执行意图；成功后原子提交产物引用与下一状态。
- 每个阶段按 `job_id + stage + input fingerprint` 幂等；发现有效产物则复用。
- 暂时错误进入 `retry_wait`，保存 `next_retry_at`；超过上限进入 `failed`。
- Engine 重启时，租约过期的运行状态由恢复器检查产物后重入当前阶段或安全回退。
- 进度只单调增加；重试可保持进度但不得伪装为完成。
- 同一 URL、模式和配置指纹可复用近期完成/运行任务；复用窗口由用户配置。

## 取消

- `queued`/`retry_wait` 可直接原子转为 `cancelled`。
- 运行阶段写 `cancel_requested_at`；Worker 在下载块、ASR 分段、LLM 请求之间的安全点检查。
- `generating_report` 原子替换开始后不中断文件提交，之后取消索引或标记完成，以避免半份报告。
- `completed`、`failed`、`cancelled` 不可再次取消，返回幂等结果。

## 产物规则

- 临时文件与最终报告使用不同目录/扩展名。
- Markdown 在目标目录创建临时文件，fsync/校验后原子替换。
- 默认不覆盖内容哈希与上次生成哈希不一致的用户文件；产生冲突副本并提示。
- 索引失败不得丢失报告：Job 可在 `indexing` 重试并保留已生成报告。

## 稳定错误码初表

`INVALID_URL`、`UNSUPPORTED_PLATFORM`、`METADATA_FETCH_FAILED`、`SUBTITLE_UNAVAILABLE`、`ASR_DISABLED`、`DOWNLOAD_LIMIT_EXCEEDED`、`DISK_SPACE_LOW`、`FFMPEG_MISSING`、`ASR_MODEL_MISSING`、`LLM_NOT_CONFIGURED`、`LLM_AUTH_FAILED`、`LLM_RATE_LIMITED`、`REPORT_CONFLICT`、`INDEX_FAILED`、`ENGINE_RESTARTED`、`MAX_ATTEMPTS_EXCEEDED`、`CANCELLED_BY_USER`、`INTERNAL_ERROR`。

