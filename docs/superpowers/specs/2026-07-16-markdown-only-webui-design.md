# Markdown-only WebUI 设计说明

## 目标

将 WebUI 收敛为“上传最终 Markdown 口播稿后直接生成视频”的工作流。用户不需要在 WebUI 中配置或调用 LLM 来生成标题、口播文案、重点词或素材搜索词；这些内容全部从上传文件读取并回显。

## 范围

### WebUI 中移除的入口

- 设置弹窗中的 LLM Provider、API Key、Base URL、Model Name 和连接测试入口；
- 视频文案面板中的主题输入；
- 脚本语言、段落数量和高级脚本提示词；
- “生成视频文案和关键词”按钮；
- “生成视频关键词”按钮；
- 可手动编辑的主题、口播文案和关键词输入框。

### WebUI 中保留的入口

- Markdown `.md` 文件上传；
- Markdown 解析错误提示和清空上传按钮；
- 标题、行数、场景数预览；
- 每行口播文案与重点词回显；
- 每个场景的行范围与素材搜索词回显；
- 素材源 API 配置、缓存设置和界面设置；
- 视频比例、素材源、音频、字幕、重点词动效、音效和合成设置。

## 数据流

```text
上传 UTF-8 Markdown
    -> parse_markdown_upload
    -> 回显标题、行文案、重点词、场景搜索词
    -> apply_to_video_params
    -> task.start 直接读取 markdown_script
    -> CosyVoice3 / 字幕对齐 / 在线素材 / 视频合成
```

当 `params.markdown_script` 存在时：

1. 标题和逐行文案由 Markdown 文档提供；
2. 素材搜索词由 Markdown 场景提供；
3. 重点词由每行重点词提供；
4. `task.start` 不调用 `llm.generate_script`、`llm.generate_terms` 或 `llm.generate_emphasis_terms`；
5. 后端 LLM 模块和已有接口暂时保留，但不属于 Markdown-only WebUI 的运行路径。

## 设计决策

采用“隐藏 WebUI 入口、保留后端兼容”的方式。这样可以满足当前无需 LLM 的视频生成要求，同时不删除已有后端模块、配置字段和单独 API，避免把本次 UI 工作扩大为不必要的接口迁移。

## 错误处理

- 未上传文件时，生成按钮不可用或显示“请先上传 Markdown 口播稿”；
- Markdown 结构错误时，继续显示解析错误，不进入生成任务；
- 首行没有素材搜索词、在线素材源未选择或素材 API Key 缺失时，沿用现有明确错误；
- LLM 未配置不应阻止 Markdown-only WebUI 加载，也不应触发连接测试或 LLM 调用。

## 验证

1. WebUI 源码中不再渲染 LLM Settings、主题、生成文案、生成关键词和高级提示词控件；
2. Markdown 上传回显仍包含标题、全部行文案、重点词和场景搜索词；
3. Markdown 任务测试用禁止调用的 LLM 替身运行时仍能完成任务流程；
4. 现有 Markdown 解析器测试、WebUI Markdown 导入测试和任务测试通过；
5. 无 LLM API Key 的配置下，WebUI 模块能够导入，Markdown 解析与任务参数映射能够运行。
