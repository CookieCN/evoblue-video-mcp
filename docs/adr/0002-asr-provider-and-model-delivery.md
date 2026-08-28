# ADR 0002: Pluggable ASR providers and out-of-band model delivery

- Status: Accepted
- Date: 2026-08-26

## Context

无字幕视频需要本地 ASR 兜底。但把 ASR 引擎和模型权重塞进基础安装包会显著放大体积；Hugging Face 与 GitHub Releases 在中国大陆的下载不稳定；中文、英文、日韩、粤语等多语言覆盖与本地隐私、小安装包、可靠下载五者无法由单一模型同时满足。默认引擎的选择必须由 EvoBlue 自有样本的质量门禁决定，而非厂商基准背书。

## Decision

- ASR 是 Local Engine 内的可插拔 Provider 边界（`ASRProvider` 协议）；Pipeline 与 Worker 不直接 import 任何具体 ASR 库。
- 平台字幕优先；仅当缺少可用字幕时才进入 ASR。
- 基础安装包不携带任何模型权重；模型由 Model Manager 按需下载、断点续传、SHA-256 校验、staging 解压后原子安装，并保留上一可用版本用于升级回滚。
- 每个模型 Manifest 记录稳定的 model/version、provider 兼容性、语言、压缩/安装体积、有序下载源、SHA-256、许可证与署名、上游 URL 与平台要求。
- 下载源采用「中国源优先 + 上游回退」；禁止把 Hugging Face 或 GitHub Releases 作为中国大陆唯一来源。
- 默认引擎是否采用 SenseVoice 由 EvoBlue 自有基准门禁决定（中文 CER、时间戳、CPU 速度、峰值内存、大陆下载链路、许可证与署名、基础包增量七项）。

## Consequences

- 字幕型工作流永不触发模型下载；缺少模型时 Job 进入 `waiting_for_model` 而非静默失败或后台下载。
- Pipeline 与具体 ASR 库解耦，新增/替换引擎不触及核心流程。
- 模型交付需要额外的下载、校验、原子安装、升级回滚与卸载基础设施（ASR-2）。
- 质量门禁在基准语料与评分命令落地前不可放行默认引擎。

## Rejected alternatives

- **默认捆绑 faster-whisper**：基础安装包被 CTranslate2/native 依赖显著放大，且非中文最优。
- **仅依赖 Hugging Face / GitHub Releases 下载**：中国大陆不可靠。
- **云 ASR 静默兜底**：违反 local-first 与隐私边界。
- **在 Pipeline 里直接 import sherpa-onnx**：破坏 Provider 边界，多引擎无法共存。
