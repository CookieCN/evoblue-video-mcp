# ASR Model Licenses & Attribution

ASR-1 引入的精确模型制品、已知许可证证据与署名清单。基础安装包**仅**随包附带 Silero VAD（MIT、可再分发、钉 SHA 校验的受管运行时依赖，见下方 VAD 一节）；识别模型 Lite/Standard 与 Whisper Base 权重不随包分发，由 Model Manager（ASR-2）按需下载、校验后安装。本清单用于安装前展示与第三方声明，禁止生产 Manifest 下载同时含 FP32 与 INT8 权重的 SenseVoice 完整包。

本文件是工程合规清单，不是法律意见。代码仓库许可证、训练数据许可证和模型权重许可证不得相互代替；只有精确制品的再分发条款、署名要求和中国下载源全部留证后，Manifest 才能标记为可发布。

## Standard — SenseVoiceSmall INT8

- Tier：Standard（中文、英、日、韩、粤）
- Model：SenseVoiceSmall（int8 量化）
- 来源：https://github.com/FunAudioLLM/SenseVoice （经 sherpa-onnx 转换，`iic/SenseVoiceSmall`）
- 制品：`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2`
- 下载体积：约 163 MB；安装体积：约 228 MB（`model.int8.onnx`）
- 制品内许可证证据：归档 `LICENSE` 仅写明参考 FunASR License，并未附带 Apache-2.0 文本
- 适用许可：FunASR Model Open Source License Agreement 1.1；使用、复制、修改和分享时必须注明出处与作者并保留相关模型名称
- 再分发状态：**暂不落镜像**（2026-08-27 决定）；FunASR 1.1 允许署名分享，但 EvoBlue 镜像仍需记录署名满足方式 + 同 SHA 中国宿主实测，见 `docs/ASR_CHINA_SOURCE_QUALIFICATION.md`
- Attribution：FunAudioLLM (Alibaba) — https://github.com/FunAudioLLM/SenseVoice
- License source：https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE

## Lite — Zipformer CTC small zh INT8

- Tier：Lite（仅中文）
- Model：Zipformer CTC small zh（int8 量化）
- 来源：k2-fsa / icefall，WenetSpeech 训练
- 制品：`sherpa-onnx-zipformer-ctc-small-zh-int8-2025-07-16.tar.bz2`
- 下载体积：约 50.5 MB；安装体积：约 62.7 MB（`model.int8.onnx`）
- 制品内许可证证据：精确 sherpa-onnx 归档未包含 `LICENSE` 或模型卡
- 上游证据：icefall 与 WenetSpeech 代码仓库为 Apache-2.0；WeNet 明确说明预训练模型遵循对应训练数据集许可证。这些证据不能单独证明该转换制品的再分发条款
- 再分发状态：**暂不落镜像**（归档无许可证文件）；须向精确制品发布者确认再分发条款后才能配置镜像，见 `docs/ASR_CHINA_SOURCE_QUALIFICATION.md`
- Attribution：k2-fsa sherpa-onnx / icefall — https://github.com/k2-fsa/icefall
- Evidence sources：https://github.com/wenet-e2e/wenet/blob/main/docs/pretrained_models.md 、https://github.com/wenet-e2e/WenetSpeech 、https://github.com/k2-fsa/icefall/blob/master/LICENSE

## VAD — Silero VAD

- Model：`silero_vad.onnx`
- 来源：https://github.com/snakers4/silero-vad（经 k2-fsa sherpa-onnx 模型发布件获取）
- 体积：643,854 bytes（约 0.64 MB）
- SHA-256：`9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`
- 许可证：MIT
- 交付方式：**随基础包分发**（`src/evoblue_video_mcp/asr/assets/`），不进入 Model Manager Manifest——它是 sherpa 两级的运行时依赖，不是用户可安装/卸载的模型；识别归档本身不含 VAD，缺失 VAD 必须阻止 Provider 注册而不是转写中途失败。SHA 在 `asr/vad.py` 钉死并在注册前校验（fail-closed），详见 `asr/assets/silero_vad.README.md`
- 再分发状态：允许再分发；发布时保留版权与 MIT 许可文本
- Attribution：Silero (snakers4) — https://github.com/snakers4/silero-vad
- License source：https://github.com/snakers4/silero-vad/blob/master/LICENSE

## Multilingual fallback — whisper.cpp Base

- Tier：Multilingual fallback（SenseVoice 覆盖外语言）
- Model：OpenAI Whisper Base，ggml-org 转换的 `ggml-base.bin`
- 精确版本：Hugging Face commit `80da2d8bfee42b0e836fc3a9890373e5defc00a6`
- 下载/安装体积：147,951,465 bytes
- SHA-256：`60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe`
- 许可证：MIT（模型仓库 Model Card 与 whisper.cpp 代码均标记 MIT）
- 再分发状态：`upstream_only`；只允许钉死的原发布者 Hugging Face URL，不配置未审批镜像
- Runtime：模型包不携带 `whisper-cli`；用户需显式配置本机可执行文件，基础安装包不增加 whisper.cpp 二进制
- Attribution：OpenAI Whisper / ggml-org whisper.cpp
- Evidence：https://github.com/ggml-org/whisper.cpp/blob/master/models/README.md 、https://huggingface.co/ggerganov/whisper.cpp
