# Third-Party Notices

本项目计划使用的第三方依赖及其许可证将在锁定依赖和发布构建时由自动化清单生成并审查。P0 不捆绑二进制依赖、FFmpeg、Whisper 模型或旧 EvoBlue 代码。

ASR 模型权重（SenseVoiceSmall、Zipformer CTC、Silero VAD、Whisper Base ggml）的许可证与署名清单见 [docs/ASR_MODEL_LICENSES.md](docs/ASR_MODEL_LICENSES.md)。

## 随包分发的模型资产

自 ASR-3 起，基础安装包随包附带且仅附带一个模型资产：Silero VAD（`src/evoblue_video_mcp/asr/assets/silero_vad.onnx`，SHA-256 `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`）。它是 sherpa-onnx 两级识别模型的运行时依赖，按 MIT 许可证再分发，完整许可文本随包存放于 `src/evoblue_video_mcp/asr/assets/silero_vad.LICENSE`，全文如下。其余模型权重不随包分发。

### Silero VAD — MIT License

Copyright (c) 2020-present Silero Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
