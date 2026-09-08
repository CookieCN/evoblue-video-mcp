# Third-Party Notices

自 ASR-4 起，本文件随发布包分发（`scripts/build_package.py` 拷入 bundle 根目录）。以下为当前锁定（`uv.lock`，2026-09-08）的主要运行时依赖与许可证；传递依赖的完整清单以 `uv.lock` 为准。模型权重（SenseVoiceSmall、Zipformer CTC、Qwen3-ASR、Silero VAD、Whisper Base ggml）的许可证与署名清单见 [docs/ASR_MODEL_LICENSES.md](docs/ASR_MODEL_LICENSES.md)。

## 随包运行时依赖（full 变体；base 变体不含 sherpa-onnx / onnxruntime / numpy）

| 组件 | 锁定版本 | 许可证 | 用途 |
|---|---|---|---|
| sherpa-onnx | 1.13.6 | Apache-2.0 | 本地 ASR 推理运行时 |
| onnxruntime | 1.29.0 | MIT | ONNX 推理（Windows 下固定 ≥1.27 规避 System32 旧 DLL） |
| numpy | 2.2.6 | BSD-3-Clause | 音频采样处理 |
| fastapi | 0.141.1 | MIT | HTTP API |
| starlette | 1.6.0 | BSD-3-Clause | ASGI 框架 |
| uvicorn | 0.52.4 | BSD-3-Clause | ASGI 服务器 |
| sqlalchemy | 2.0.52 | MIT | ORM / 迁移 |
| aiosqlite | 0.22.1 | MIT | 异步 SQLite 驱动 |
| httpx | 0.28.1 | BSD-3-Clause | HTTP 客户端（模型下载、LLM） |
| yt-dlp | 2026.8.19 | Unlicense | 平台元数据与字幕抓取 |
| keyring | 25.7.0 | MIT | 系统凭据库 |
| mcp | 2.0.0 | MIT | MCP 协议（P4 STDIO Bridge） |
| pydantic / pydantic-settings | 2.13.4 / 2.15.0 | MIT | 合同与配置 |
| platformdirs | 4.11.4 | MIT | 平台标准数据目录 |
| structlog | 25.5.0 | Apache-2.0 / MIT | 结构化日志 |
| tenacity | 9.1.4 | Apache-2.0 | 重试 |

代码仓库自身许可证为 Apache-2.0（见 `LICENSE`）。P0 约定继续有效：不捆绑 FFmpeg、Whisper 模型或旧 EvoBlue 代码。

## 随包分发的模型资产

自 ASR-3 起，基础安装包随包附带且仅附带一个模型资产：Silero VAD（`src/evoblue_video_mcp/asr/assets/silero_vad.onnx`，SHA-256 `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`）。它是 sherpa-onnx 本地识别模型的运行时依赖，按 MIT 许可证再分发，完整许可文本随包存放于 `src/evoblue_video_mcp/asr/assets/silero_vad.LICENSE`，全文如下。其余模型权重（包括 Qwen3-ASR）不随包分发。

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
