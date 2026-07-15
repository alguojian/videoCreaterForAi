# videoCreaterForAi

Windows 原生环境、由结构化 Markdown 口播稿驱动的本地口播视频生成工具。

## 当前主要功能

- 结构化 Markdown 直传；
- CosyVoice3 本地语音合成；
- Qwen3 Forced Aligner 字幕对齐；
- Pexels / Pixabay / Coverr 在线素材；
- 横屏 16:9 比例过滤和尽量免裁剪合成；
- 底部单行白字黑描边字幕；
- 同行多重点词、重点词可跨行重复触发、随机位置/颜色/动画、固定音效库；
- 格式化 Markdown 已提供搜索词与重点词，视频生成阶段不再调用 LLM。

## 使用流程

整理 Markdown -> WebUI 上传并预览 -> 本地生成旁白和字幕 -> 按场景搜索素材 -> 合成并导出视频

## Windows 最短启动方式

优先双击 `webui.bat`。

备用命令：

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m streamlit run webui\Main.py
```

## 文档与示例

- [Markdown 口播稿格式说明](docs/markdown-script-format.md)
- [Markdown 口播稿示例](examples/markdown-scripts/marriage.md)
