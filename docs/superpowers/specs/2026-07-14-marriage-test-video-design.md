# 婚姻主题本地语音测试视频设计

## 目标

生成一条可直接观看的中文竖屏测试视频，验证本地 CosyVoice3 旁白、Qwen ForcedAligner 字幕和 MoneyPrinterTurbo 最终 MP4 合成链路。

## 方案

采用项目内资源和本地生成画面，不下载外部音频：

- 主题：婚姻是共同经营，而不是互相改变。
- 旁白：`local:default`，CosyVoice3 RL 模型，中文文本约 30 秒。
- 字幕：Qwen ForcedAligner 生成 SRT，再由视频合成阶段烧录。
- 画面：FFmpeg 生成暖色、简洁的竖屏背景，避免外部素材版权和网络不稳定。
- 字体：项目内 `resource/fonts/MicrosoftYaHeiBold.ttc`。
- 输出：`storage/tasks/marriage-test/final-1.mp4`，同时保留 WAV、SRT 和生成清单。

## 验收

- 输出 MP4 存在且可被 FFmpeg 读取。
- 视频包含音频轨道和烧录字幕。
- 旁白和字幕均来自本地 Worker；失败时明确报错，不调用云端 TTS。
