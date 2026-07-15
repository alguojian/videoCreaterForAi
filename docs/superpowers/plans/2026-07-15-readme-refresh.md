# videoCreaterForAi Documentation Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inherited project README with a concise Chinese overview of the current Windows-native video workflow and add a precise Markdown script format guide that people and AI tools can follow.

**Architecture:** Keep the repository landing page intentionally short and route detailed authoring rules to one focused document. Treat `app/services/script_document.py` as the source of truth, and verify the published example with the real parser instead of duplicating undocumented assumptions.

**Tech Stack:** Markdown, Windows PowerShell, Python 3.11, pytest, Git

---

## File map

- Modify `README.md`: project positioning, current features, five-step workflow, Windows launch command, and links to the authoring guide and sample.
- Create `docs/markdown-script-format.md`: exact upload grammar, field rules, scene inheritance, emphasis behavior, complete sample, reusable AI prompt, and pre-upload checklist.
- Reuse `examples/markdown-scripts/marriage.md`: parser-backed example linked from both documents; do not create a second drifting sample file.

### Task 1: Replace the inherited README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace all inherited content with the current project identity**

Use `videoCreaterForAi` as the only project title and describe it as a Windows-native local talking-head/video production tool driven by a structured Markdown script.

- [ ] **Step 2: Publish only the current major capabilities**

Document these capabilities and no inherited provider catalogue:

```text
结构化 Markdown 直传
CosyVoice3 本地语音合成
Qwen3 Forced Aligner 字幕对齐
Pexels / Pixabay / Coverr 在线素材
横屏 16:9 比例过滤和尽量免裁剪合成
底部单行白字黑描边字幕
多重点词、随机位置/颜色/动画、固定音效库
格式化脚本生成阶段不调用 LLM
```

- [ ] **Step 3: Add the shortest usable workflow and Windows entry point**

Show this workflow:

```text
整理 Markdown -> WebUI 上传并预览 -> 本地生成旁白和字幕 -> 按场景搜索素材 -> 合成并导出视频
```

Show `webui.bat` as the primary Windows launcher and this direct command as the configured-environment fallback:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m streamlit run webui\Main.py
```

- [ ] **Step 4: Link the detailed guide and parser-backed sample**

Add relative links to `docs/markdown-script-format.md` and `examples/markdown-scripts/marriage.md`.

- [ ] **Step 5: Scan for inherited README content**

Run:

```powershell
rg -n "MoneyPrinterTurbo|harry0703|赞助|Colab|Docker|Star History|作品展示" README.md
```

Expected: no matches.

### Task 2: Add the Markdown script authoring guide

**Files:**
- Create: `docs/markdown-script-format.md`
- Reference: `app/services/script_document.py`
- Reference: `examples/markdown-scripts/marriage.md`

- [ ] **Step 1: Document the only accepted file structure**

State that uploads must be UTF-8 `.md` files containing exactly one non-empty H1 followed by exactly one pipe-wrapped four-column table. Use this exact header:

```markdown
| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
```

State that no prose, secondary heading, list, note, or content after the table is allowed.

- [ ] **Step 2: Document every row validation rule**

Include all parser rules:

```text
序号从 1 开始连续递增
口播文案不能为空
重点词用中文或英文分号分隔
重点词忽略空白和标点后仍必须存在于同行口播文案中
素材搜索词用中文或英文分号分隔，但短语本身必须是英文
每行最多 3 个素材搜索词
第一行必须提供素材搜索词
重复的重点词或搜索词会按原顺序去重并产生预览警告
```

- [ ] **Step 3: Explain scene grouping and emphasis behavior**

Explain that a row with search terms starts a new scene and subsequent rows with an empty search cell inherit that scene until the next populated search cell. Explain that multiple emphasis terms can appear on one row, and the same term may be repeated on different rows to trigger again.

- [ ] **Step 4: Add a complete valid example and a reusable AI conversion prompt**

Use the current marriage example as the complete valid table. The prompt must instruct the AI to:

```text
保留核心观点并整理成自然口语
每行只表达一个适合单行字幕的完整语义单元
只标记实际出现在同行文案中的重点词
按连续画面语义划分场景，不要求每行都搜索素材
每个新场景给出 1 至 3 个具体英文搜索短语
严格输出一个 H1 和固定四列表格
不输出解释、代码围栏或表格外内容
```

- [ ] **Step 5: Add a pre-upload checklist and common error table**

Cover extension/encoding, extra content, incorrect headers, skipped row numbers, emphasis mismatch, Chinese search terms, too many search terms, and an empty first-row search cell.

### Task 3: Verify, commit, and publish the documentation

**Files:**
- Verify: `README.md`
- Verify: `docs/markdown-script-format.md`
- Verify: `examples/markdown-scripts/marriage.md`
- Test: `test/services/test_script_document.py`

- [ ] **Step 1: Verify links and forbidden inherited content**

Run:

```powershell
$paths = 'docs/markdown-script-format.md','examples/markdown-scripts/marriage.md','webui.bat'
$paths | ForEach-Object { if (-not (Test-Path -LiteralPath $_)) { throw "Missing README target: $_" } }
rg -n "MoneyPrinterTurbo|harry0703|赞助|Colab|Docker|Star History|作品展示" README.md
```

Expected: all paths exist and `rg` returns no matches.

- [ ] **Step 2: Parse both published examples with the production parser**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -c "from pathlib import Path; from app.services.script_document import parse_markdown_script; [print(p, len(parse_markdown_script(Path(p).read_text(encoding='utf-8')).rows)) for p in ('examples/markdown-scripts/marriage.md',)]"
```

Expected: `examples/markdown-scripts/marriage.md 6`.

- [ ] **Step 3: Run parser regression tests and whitespace validation**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_script_document.py -q
git diff --check
```

Expected: all tests pass and `git diff --check` is silent.

- [ ] **Step 4: Commit only the documentation refresh**

Run:

```powershell
git add README.md docs/markdown-script-format.md docs/superpowers/plans/2026-07-15-readme-refresh.md
git commit -m "docs: refresh project overview"
```

- [ ] **Step 5: Push and confirm the remote branch**

Run:

```powershell
git push origin Master
git rev-parse HEAD
git ls-remote --heads origin Master
```

Expected: local `HEAD` and `refs/heads/Master` report the same commit hash.
