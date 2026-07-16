# Windows 原生实施与配置手册

本文用于指导其他人从 GitHub 拉取项目后，在 Windows 原生环境完成安装、配置和首次生成。

本项目当前的 WebUI 工作流是：上传最终 Markdown 口播稿，直接读取标题、口播文案、重点词和素材搜索词后生成视频。这个流程不需要配置 LLM，也不会调用 LLM 生成标题、文案或关键词。

## 1. 运行前提

建议准备：

- Windows 10 或 Windows 11；
- Git；
- PowerShell；
- Python 3.11，或安装 `uv` 用于创建项目环境；
- 稳定网络，用于安装依赖、访问在线素材源和下载模型；
- 足够的磁盘空间。CosyVoice3 和 Qwen 对齐模型不放在 Git 仓库中，需要单独下载。

以下内容不会随 Git 一起提交，每台电脑都需要单独准备：

- `.venv` 或 Conda 环境；
- `config.toml`；
- `tools/` 下的本地 Python 环境；
- `resource/models/` 下的模型；
- Pexels、Pixabay、Coverr 等 API Key。

## 2. 拉取项目并安装主环境

打开 PowerShell，执行：

```powershell
git clone https://github.com/alguojian/videoCreaterForAi.git
cd videoCreaterForAi
uv sync --frozen
```

如果没有安装 `uv`，也可以使用 Python 3.11 创建虚拟环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

主环境负责 WebUI、视频合成、字幕处理、在线素材下载和普通 TTS。它不等于 CosyVoice/Qwen 的本地推理环境，后者需要单独配置。

## 3. 创建配置文件

首次启动时，程序会根据 `config.example.toml` 自动创建 `config.toml`。也可以提前手动复制：

```powershell
if (-not (Test-Path .\config.toml)) {
    Copy-Item .\config.example.toml .\config.toml
}
```

`config.toml` 已被 `.gitignore` 忽略，不要把包含 API Key 的配置文件提交到 GitHub。

## 4. 启动 WebUI

推荐使用项目启动脚本：

```powershell
.\webui.bat
```

也可以直接启动：

```powershell
uv run streamlit run webui\Main.py
```

如果使用手动创建的 `.venv`：

```powershell
.\.venv\Scripts\python.exe -m streamlit run webui\Main.py
```

默认访问地址为 `http://localhost:8501`。如果端口被占用，`webui.bat` 会在 8501—8599 中选择可用端口并打印实际地址。

## 5. 配置在线素材 API

在 WebUI 中打开“设置”，填写至少一个在线素材源的 API Key：

- Pexels；
- Pixabay；
- Coverr。

当前 Markdown 生成流程必须使用在线素材源。选择 Markdown 文件后，如果视频素材源设置为本地文件，生成按钮会被拦截并提示切换到 Pexels、Pixabay 或 Coverr。

因此，其他人即使不配置 LLM，也至少需要配置一个在线素材 API Key，才能完成“Markdown 口播稿 -> 视频”的完整流程。只查看页面、上传并预览 Markdown 不需要素材 API Key。

## 6. 准备并上传 Markdown 口播稿

Markdown 文件必须符合固定格式：

- UTF-8 编码，扩展名为 `.md`；
- 一个一级标题；
- 一个固定四列表格；
- 表格列为：序号、口播文案、重点词、素材搜索词；
- 第一行必须填写素材搜索词；
- 连续几行可以共用一个场景，不需要每行都填写搜索词；
- 重点词必须真实出现在同行文案中，去除空白和标点后至少包含 2 个可见字符；
- 素材搜索词使用英文短语，每行最多 3 个。

完整格式、转换提示词和示例见：

- [Markdown 口播稿格式说明](markdown-script-format.md)；
- [婚姻主题示例](../examples/markdown-scripts/marriage.md)。

上传后，WebUI 会回显标题、口播行、重点词和场景搜索词。有效 Markdown 上传后才能点击“生成视频”；清空或解析失败时，生成按钮会保持禁用。

## 7. 配置配音方式

### 7.1 普通 TTS

如果只想先验证整体流程，可以先使用 WebUI 中的普通 TTS。部分云端 TTS 需要 API Key，Edge TTS 通常不需要单独的 API Key，但需要网络连接。

### 7.2 Windows 本地 CosyVoice3 + Qwen 对齐

如果要使用本地 CosyVoice3 和 Qwen3 Forced Aligner，需要额外安装 Miniforge 或 Miniconda，然后执行：

```powershell
.\scripts\windows\setup_local_voice.ps1
```

该脚本会创建以下环境：

- `mpt`：项目主环境；
- `cosyvoice`：CosyVoice 推理环境；
- `qwen-aligner`：Qwen 强制对齐环境。

脚本结束时会打印 `mpt_python`、`cosyvoice_python` 和 `aligner_python` 三个实际路径，请保存这些路径。使用脚本创建的 `mpt` 环境启动 WebUI 时，可以直接执行输出的 `mpt_python` 路径；使用 `uv` 或 `.venv` 作为主环境时，则继续使用第 4 节的启动方式。

模型下载是独立步骤：

```powershell
.\scripts\windows\download_local_voice_models.ps1 `
    -PythonExecutable <主环境 Python 路径> `
    -ModelRoot .\resource\models
```

下载完成后应至少存在：

```text
resource/models/Fun-CosyVoice3-0.5B-2512
resource/models/Qwen3-ForcedAligner-0.6B
```

在 `config.toml` 的 `[local_voice]` 中：

1. 将 `enabled` 改为 `true`；
2. 将 `cosyvoice_python` 改为实际的 CosyVoice 环境 Python 路径；
3. 将 `aligner_python` 改为实际的 Qwen 环境 Python 路径；
4. 确认 `cosyvoice_model_dir` 和 `aligner_model_dir` 指向实际模型目录；
5. 确认两个 worker 脚本存在。

配置示例：

```toml
[local_voice]
enabled = true
provider = "cosyvoice3"
subtitle_provider = "qwen_forced_aligner"
cosyvoice_python = "C:/Users/你的用户名/miniforge3/envs/cosyvoice/python.exe"
aligner_python = "C:/Users/你的用户名/miniforge3/envs/qwen-aligner/python.exe"
cosyvoice_worker = "workers/cosyvoice_worker/generate.py"
aligner_worker = "workers/qwen_aligner_worker/align.py"
cosyvoice_model_dir = "resource/models/Fun-CosyVoice3-0.5B-2512"
aligner_model_dir = "resource/models/Qwen3-ForcedAligner-0.6B"
```

示例中的用户名和路径必须替换为本机实际路径。配置后可以运行环境检查：

```powershell
.\scripts\windows\check_local_voice_env.ps1 `
    -MptPython <mpt_python 输出的路径> `
    -CosyvoicePython C:\Users\你的用户名\miniforge3\envs\cosyvoice\python.exe `
    -AlignerPython C:\Users\你的用户名\miniforge3\envs\qwen-aligner\python.exe
```

本地模型较大，首次推理还可能需要等待模型加载。建议先用短 Markdown 文案做一次测试。

## 8. 视频生成建议流程

1. 启动 WebUI；
2. 在“设置”中填写 Pexels、Pixabay 或 Coverr API Key；
3. 上传 UTF-8 Markdown 文件；
4. 检查标题、文案、重点词、场景和搜索词预览；
5. 选择视频素材源和横屏 16:9 比例；
6. 选择 TTS。使用本地 CosyVoice 时，先确认本地环境检查通过；
7. 按需要启用字幕、重点词动效和固定音效；
8. 点击“生成视频”。

重点词文件字段会自动解析和回显，但重点词动画仍由“启用重点词动效”开关控制；这样可以避免上传普通重点词后默认产生大量动画。固定音效来自项目内资源，不需要每次调用模型生成。

## 9. 常见问题

### 找不到 `tools/miniforge3/.../python.exe`

这是正常的：`tools/` 不在 Git 仓库中。普通 WebUI 启动应使用 `uv` 或 `.venv`；只有本地 CosyVoice/Qwen 才需要单独配置它们的 Python 路径。

### WebUI 可以打开，但点击生成提示缺少 API Key

在“设置 -> 素材 API”中填写至少一个在线素材源的 Key。Markdown 流程不能使用本地素材源替代在线素材源。

### 提示 Markdown 解析失败

检查文件编码、扩展名、一级标题、四列表头、分隔行、序号、重点词和第一行搜索词。优先按照 [Markdown 格式说明](markdown-script-format.md) 重新整理。

### 提示本地语音环境不可用

检查 `config.toml` 中的 `local_voice.enabled`、两个 Python 路径和两个模型目录。不要直接照抄 `config.example.toml` 中的示例路径；应改成实际安装路径。

### 提示找不到 FFmpeg

项目通常会自动检测或下载 FFmpeg。若自动处理失败，请安装 Windows 版 FFmpeg，或在 `config.toml` 的 `[app]` 中设置正确的 `ffmpeg_path`。

## 10. 安全与提交规范

- 不要提交 `config.toml`；
- 不要把 API Key 写入 Markdown、README 或脚本；
- 不要把本地模型目录和虚拟环境提交到 Git；
- 共享配置时只修改 `config.example.toml`，不要复制真实密钥。
