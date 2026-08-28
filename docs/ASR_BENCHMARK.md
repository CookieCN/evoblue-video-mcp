# ASR Benchmark

## 目的

用 EvoBlue 自有样本对 ASR Provider 做可复现的质量与边界评分，作为「默认引擎能否放行」的门禁输入。不使用厂商基准单方面背书；真实音频/字幕样本遵循 `tests/evals/README.md` 的许可边界，不得提交私人视频、Cookie 或凭据。

## 命令

```bash
# 冒烟（无模型依赖）
uv run python -m evoblue_video_mcp.asr.benchmark tests/fixtures/asr_benchmark/corpus.json --provider fake

# 真实门禁（走生产注册链路：模型文件 + 随包 VAD SHA 校验）
uv run python scripts/build_benchmark_corpus.py --out benchmarks/corpus
uv run python -m evoblue_video_mcp.asr.benchmark benchmarks/corpus/corpus.json \
  --provider standard --gate --out benchmarks/results/standard-v1.json
```

- 第一个位置参数 `<corpus.json>`：基准语料 Manifest 路径。
- `--provider`：`fake`（确定性假引擎）| `standard`（SenseVoice INT8）| `lite`（Zipformer CTC INT8）。
- `--models-dir`：模型安装根目录（默认 `platformdirs` 用户数据目录）。
- `--gate`：按 `asr/release_gate.py` 冻结阈值评估，任一项不达标退出码为 1；评估项写入报告 `gate` 字段。
- `--out report.json`：把报告写入文件便于对比；报告含平台、CPU 数、阈值版本、峰值内存。
- 语料样本可声明 `entities`（实体召回）、空 `reference_text`（静音/纯音乐，按幻觉计分）。
- CER 用去标点归一化文本（NFKC + 小写），WER 用保留词边界的归一化；两种口径见 `metrics.normalize_for_scoring` / `metrics.normalize_words`。

## 语料 Manifest 字段

```json
{
  "name": "evoblue-asr-smoke",
  "version": "1",
  "samples": [
    {
      "id": "zh-studio-001",
      "description": "Mandarin studio speech",
      "language": "zh",
      "reference_text": "大家好，欢迎收看本期节目。",
      "audio_path": null
    }
  ]
}
```

- `id`：样本稳定标识，报告按此 keyed。
- `language`：语言标签（ISO 639-1 或 BCP-47）。
- `reference_text`：打分真值文本。
- `audio_path`：音频文件路径；Fake Provider 阶段可省略，真实引擎必须提供。

## 评分口径

- `cer`：字符错误率（Levenshtein / 参考字符数），中文适用。
- `wer`：词错误率（按空白分词，Levenshtein / 参考词数），英文适用。
- 边界误差（segment boundary error）：起止时间戳平均绝对偏差，由 `metrics.segment_boundary_error` 提供，接入真实时间戳样本后启用。

## 接入真实引擎

`_build_provider` 支持 `fake` 与两个内置 sherpa tier（经 `register_available_asr_providers` 生产注册链路）。新增其他引擎时在同一注册表接入实现 `asr/base.py` 的 `ASRProvider` 协议的 Provider，并提供带 `audio_path` 与参考文本的语料。

## 门禁与审批的关系

基准报告是门禁的输入，不是审批本身。审批记录在 `asr/approvals.py`（精确 `(model_id, version)` 键），证据与结论见 `docs/ASR_RELEASE_GATE.md`。换语料、换阈值版本或换模型版本都必须重跑门禁并更新审批。
