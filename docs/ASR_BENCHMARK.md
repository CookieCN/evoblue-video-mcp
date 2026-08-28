# ASR Benchmark

## 目的

用 EvoBlue 自有样本对 ASR Provider 做可复现的质量与边界评分，作为「默认引擎能否放行」的门禁输入。不使用厂商基准单方面背书；真实音频/字幕样本遵循 `tests/evals/README.md` 的许可边界，不得提交私人视频、Cookie 或凭据。

## 命令

```bash
uv run python -m evoblue_video_mcp.asr.benchmark tests/fixtures/asr_benchmark/corpus.json --provider fake
```

- 第一个位置参数 `<corpus.json>`：基准语料 Manifest 路径。
- `--provider`：ASR Provider 注册名，当前仅 `fake`；真实引擎在 ASR-1 注册。
- `--out report.json`：把报告写入文件便于对比；同一语料与 Provider 两次运行输出字节一致即复现。

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

在 `src/evoblue_video_mcp/asr/benchmark/__main__.py` 的 `_build_provider` 注册表新增一个实现 `asr/base.py` 的 `ASRProvider` 协议的 Provider，并提供带 `audio_path` 与定时参考的语料即可。
