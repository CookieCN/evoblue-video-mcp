# ASR China Download-Source Qualification

Status: **Qwen3-ASR domestic source shipped; legacy tiers remain upstream-only**
Decision date: 2026-08-27  
Owner: Wilson Gu (product decision recorded; implementer built machinery + evidence)

## Decision

### 2026-09-08 amendment — Qwen3-ASR

Qwen3-ASR 0.6B INT8 新增为可选模型，使用 ModelScope 国内路径直接安装。它不是
Alibaba 官方原始 Transformers 权重，而是供 sherpa-onnx 使用的 ONNX INT8 导出；
EvoBlue 固定到导出仓库提交
`9c182309f7bb075f241424441add9e16c5086dfb`，逐文件钉死体积与 SHA-256，并以
`file-set` 合同下载、校验、原子激活。来源标记为 `mirror_approved/china-primary`，
不是未经校验的运行时自动下载。

2026-09-08 对最大权重文件 `encoder.int8.onnx` 的固定提交 URL 实测返回 HTTP 206：
`Content-Range: bytes 0-0/182491662`。因此国内路径支持 Range 断点续传；完整文件集
总下载/安装体积为 987,023,031 bytes（约 941 MiB）。模型管理页在用户同意前显示
此成本，基础安装包仍不携带任何 Qwen 权重。

Qwen3-ASR 当前是“可安装、可明确选择、已安装时可复用”的增强选项，不是正式默认。
在 EvoBlue 冻结语料、最低规格 Windows 机器和真实大陆网络完成独立门禁前，自动推荐
仍保持 Standard SenseVoice（中文/中英等）或 Whisper（覆盖外语言）。

证据：

- Alibaba Qwen 官方仓库：https://github.com/QwenLM/Qwen3-ASR
- sherpa-onnx Qwen3-ASR 模型/运行说明：https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html
- 固定 ONNX 导出来源：https://modelscope.cn/models/zengshuishui/Qwen3-ASR-onnx

### 2026-08-27 legacy-tier decision

ASR-2 第三步的「中国下载源资格验证」按「**器械 + 留证，暂不落镜像**」交付：

- 实现了 Manifest 多源回退（`installer` 按 `sources` 顺序下载，某源失败自动切换下一源，清掉源专属 ETag/Last-Modified、保留共享 SHA 的 partial 续传）。
- Lite Zipformer 进入 `APPROVED_CATALOG`（`upstream_only`，仅钉 GitHub 原始发布 URL）。
- 当时两个生产 Manifest 均保持 `redistribution="upstream_only"`；此历史结论仅适用于
  SenseVoice 与 Zipformer，不适用于上面的 Qwen3 新增项。
- 本文件留证「为什么现在不能落镜像」以及「将来要落镜像需要补什么」。

`is_releasable` 继续 fail-closed：没有 catalog 审批的源、没有充分使用条款的制品，一律不能进生产清单。

## Measured evidence (2026-08-27, via `scripts/qualify_china_sources.py`)

hf-mirror.com（Hugging Face 中国大陆镜像）实测可达，且对 SenseVoice int8 制品返回的散文件**体积与钉死值完全一致**，并支持 Range 断点续传：

| 候选源 | 状态 | Content-Length | Range 探针 |
|---|---|---|---|
| `hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx` | 200 | 239,233,841（与 manifest 一致） | 206 `bytes 0-1023/239233841` |
| `hf-mirror.com/…/tokens.txt` | 200 | 315,894（与 manifest 一致） | 206 `bytes 0-1023/315894` |

结论：中国镜像**可达且可断点续传**，权重内容与 GitHub 归档解压后的文件相同（体积逐一吻合），但以**散文件**形式提供，而非单个 `.tar.bz2` 归档。

## Why no mirror is shipped

1. **冻结合同约束**：Manifest 合同要求「所有 `sources` 必须是 SHA-256 完全相同的同一归档」（`_validate_artifact_consistency`）。GitHub 归档与 hf-mirror 散文件是**不同容器**，SHA 不同，无法在同一 `sources` 列表里并存——除非重开合同支持「多文件源 / 散文件布局」。
2. **Lite 许可证未决**：Zipformer CTC 精确归档**没有许可证文件**；上游 icefall/WenetSpeech 的 Apache-2.0 不能单独证明该转换制品的再分发条款。在向发布者求证前，Lite 不能配置任何镜像（`upstream_only` 仅允许原始发布 URL）。
3. **SenseVoice 法律上可行但不越权**：FunASR Model License 1.1 允许「使用/复制/修改/分享」，条件是署名（Alibaba/FunAudioLLM + 保留模型名）。但「是否落 EvoBlue 镜像」是产品/法律审批决定，本步骤只留证，不替你签。

## What a future mirror needs (checklist)

要真正落地一个 `mirror_approved` 中国源，需要逐条完成并留证：

- [ ] **SenseVoice**：记录 FunASR 1.1 署名条款满足方式（镜像页/清单里保留 Alibaba/FunAudioLLM 署名与模型名）；确定一个「同 SHA 归档」的中国宿主（ModelScope 归档 / EvoBlue CDN），并实测 SHA=钉死值。
- [ ] **Lite**：向 k2-fsa / 精确制品发布者取得可留档的再分发声明，或改用可再分发的替代归档。
- [ ] **合同**：若要支持 hf-mirror 散文件，需在 `manifest` 增加「多文件源」或「散文件布局」能力，并重新冻结合同（ASR-3 或独立变更）。
- [ ] **实测大陆画像**：真实大陆网络下跑通「中国源优先 + 上游回退」全链路（属于 ASR-4「clean-machine installation passes a mainland-China network profile」）。

在完成前，回退机制已就位：一旦某个 China-primary 源获批进入 catalog，`install_model` 无需改动即会「china-primary 优先、上游回退」。
