# Markdown Script Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a user to upload one strict Markdown oral-script file, preview it in WebUI, and generate a scene-aware video with row-bound emphasis terms without calling an LLM.

**Architecture:** Add typed Markdown document models and a focused parser/alignment service. Persist the parsed document in `VideoParams`, derive row and scene timings from the generated subtitle timeline, then route Markdown tasks through row-aware emphasis generation, scene-aware material acquisition, and scene-bounded video composition. Existing manual and AI-assisted workflows remain unchanged when no Markdown document is present.

**Tech Stack:** Python 3.11, Pydantic 2, Streamlit, MoviePy, FFmpeg, pytest/unittest, existing Pexels/Pixabay/Coverr services.

---

## File structure

- Create `app/models/script_document.py`: serialized Markdown row/document models stored in `VideoParams` and task history.
- Create `app/services/script_document.py`: strict Markdown parsing, scene grouping, subtitle-row alignment, and scene timing.
- Modify `app/models/schema.py`: add optional `markdown_script` to `VideoParams`.
- Modify `webui/Main.py`: upload, parse, preview, clear-import, generation guard, and restore logic.
- Modify all `webui/i18n/*.json`: Markdown import labels and validation messages.
- Modify `app/services/emphasis.py`: build cues from source rows and preserve repeated terms across rows.
- Modify `app/services/material.py`: download enough material per timed scene using ordered fallback terms.
- Modify `app/services/video.py`: compose downloaded materials inside exact scene boundaries.
- Modify `app/services/task.py`: select the Markdown pipeline, bypass all LLM generation, persist manifests, and preserve the legacy path.
- Create `test/services/test_script_document.py`: models, parser, validation, scene inheritance, and row timing.
- Create `test/services/test_webui_markdown_import.py`: WebUI helper and locale coverage.
- Modify `test/services/test_schema.py`, `test/services/test_emphasis.py`, `test/services/test_material.py`, `test/services/test_video.py`, and `test/services/test_task.py`.
- Create `examples/markdown-scripts/marriage.md`: valid user-facing example.
- Create `scripts/generate_markdown_marriage_video.py`: deterministic Windows-native acceptance sample.

## Task 1: Persisted Markdown document models

**Files:**
- Create: `app/models/script_document.py`
- Modify: `app/models/schema.py:60-125`
- Modify: `test/services/test_schema.py`

- [ ] **Step 1: Write the failing schema round-trip test**

Add this test to `test/services/test_schema.py`:

```python
def test_video_params_round_trips_markdown_script_document(self):
    params = VideoParams(
        video_subject="婚姻二字",
        markdown_script={
            "title": "婚姻二字",
            "rows": [
                {
                    "number": 1,
                    "text": "婚姻二字写起来只有两笔。",
                    "emphasis_terms": ["婚姻二字", "只有两笔"],
                    "material_search_terms": ["wedding couple"],
                }
            ],
        },
    )

    restored = VideoParams.model_validate(params.model_dump(mode="json"))

    self.assertEqual(restored.markdown_script.title, "婚姻二字")
    self.assertEqual(restored.markdown_script.rows[0].number, 1)
    self.assertEqual(
        restored.markdown_script.rows[0].emphasis_terms,
        ["婚姻二字", "只有两笔"],
    )
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_schema.py -k markdown_script
```

Expected: FAIL because `VideoParams` does not have a typed `markdown_script` field.

- [ ] **Step 3: Add the models and schema field**

Create `app/models/script_document.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class MarkdownScriptRow(BaseModel):
    number: int = Field(ge=1)
    text: str = Field(min_length=1)
    emphasis_terms: list[str] = Field(default_factory=list)
    material_search_terms: list[str] = Field(default_factory=list)


class MarkdownScriptDocument(BaseModel):
    title: str = Field(min_length=1)
    rows: list[MarkdownScriptRow] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    def script_text(self) -> str:
        return "\n".join(row.text for row in self.rows)
```

Import `MarkdownScriptDocument` in `app/models/schema.py` and add this field after `video_terms`:

```python
markdown_script: Optional[MarkdownScriptDocument] = None
```

- [ ] **Step 4: Run schema tests and verify GREEN**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_schema.py
```

Expected: all schema tests pass.

- [ ] **Step 5: Commit the typed persistence boundary**

```powershell
git add app/models/script_document.py app/models/schema.py test/services/test_schema.py
git commit -m "feat: persist markdown script documents"
```

## Task 2: Strict Markdown parsing and scene inheritance

**Files:**
- Create: `app/services/script_document.py`
- Create: `test/services/test_script_document.py`
- Create: `examples/markdown-scripts/marriage.md`

- [ ] **Step 1: Write parser and scene-grouping tests**

Create `test/services/test_script_document.py` with these initial tests:

```python
import pytest

from app.services import script_document


VALID_MARKDOWN = """# 婚姻二字

| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
| 1 | 婚姻二字写起来只有两笔。 | 婚姻二字；只有两笔 | wedding couple；married couple |
| 2 | 走进去却是一生的功课。 | 一生的功课 | |
| 3 | 平凡日子里也别忘了好好说话。 | 平凡日子;好好说话 | couple talking |
"""


def test_parse_markdown_builds_rows_and_inherited_scenes():
    document = script_document.parse_markdown_script(VALID_MARKDOWN)
    scenes = script_document.build_scenes(document)

    assert document.title == "婚姻二字"
    assert document.script_text().splitlines() == [
        "婚姻二字写起来只有两笔。",
        "走进去却是一生的功课。",
        "平凡日子里也别忘了好好说话。",
    ]
    assert document.rows[0].emphasis_terms == ["婚姻二字", "只有两笔"]
    assert [(scene.first_row, scene.last_row) for scene in scenes] == [(1, 2), (3, 3)]
    assert scenes[0].search_terms == ("wedding couple", "married couple")


def test_parse_markdown_deduplicates_lists_and_keeps_a_preview_warning():
    document = script_document.parse_markdown_script(
        VALID_MARKDOWN.replace(
            "婚姻二字；只有两笔",
            "婚姻二字；只有两笔；婚姻二字",
        )
    )

    assert document.rows[0].emphasis_terms == ["婚姻二字", "只有两笔"]
    assert document.warnings == ["第 1 行重点词存在重复项，已按原顺序去重"]


def test_parse_markdown_upload_requires_md_and_utf8():
    with pytest.raises(script_document.MarkdownScriptError, match=".md"):
        script_document.parse_markdown_upload("script.txt", VALID_MARKDOWN.encode("utf-8"))
    with pytest.raises(script_document.MarkdownScriptError, match="UTF-8"):
        script_document.parse_markdown_upload("script.md", b"\xff\xfe")


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        ("| 序号 | 口播文案 | 重点词 | 素材搜索词 |", "缺少一级标题"),
        (VALID_MARKDOWN.replace("\n\n| 序号", "\n\n额外说明\n\n| 序号"), "标题和表格之外"),
        (VALID_MARKDOWN.replace("| 1 |", "| 2 |", 1), "序号必须从 1 开始连续递增"),
        (VALID_MARKDOWN.replace("婚姻二字；只有两笔", "不存在的重点词"), "第 1 行重点词"),
        (VALID_MARKDOWN.replace("wedding couple；married couple", "婚礼夫妻"), "素材搜索词必须使用英文"),
        (VALID_MARKDOWN.replace("wedding couple；married couple", ""), "第一行必须填写素材搜索词"),
    ],
)
def test_parse_markdown_rejects_invalid_documents(markdown, message):
    with pytest.raises(script_document.MarkdownScriptError, match=message):
        script_document.parse_markdown_script(markdown)
```

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_script_document.py
```

Expected: collection fails because `app.services.script_document` does not exist.

- [ ] **Step 3: Implement the strict parser and scene builder**

Create `app/services/script_document.py` with these public boundaries:

```python
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.models.script_document import MarkdownScriptDocument, MarkdownScriptRow


EXPECTED_COLUMNS = ("序号", "口播文案", "重点词", "素材搜索词")
_ESCAPED_PIPE = "\x00MPT_PIPE\x00"
_ENGLISH_QUERY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '&-]*$")


class MarkdownScriptError(ValueError):
    pass


@dataclass(frozen=True)
class ScriptScene:
    index: int
    first_row: int
    last_row: int
    search_terms: tuple[str, ...]


def visible_text(value: str) -> str:
    return "".join(
        char
        for char in str(value or "")
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def _split_cell_list(
    value: str,
    row_number: int,
    field_name: str,
    warnings: list[str],
) -> list[str]:
    values = [item.strip() for item in re.split(r"[;；]", value) if item.strip()]
    unique = list(dict.fromkeys(values))
    if len(unique) != len(values):
        warnings.append(f"第 {row_number} 行{field_name}存在重复项，已按原顺序去重")
    return unique


def _split_table_row(line: str) -> list[str]:
    protected = line.strip().replace(r"\|", _ESCAPED_PIPE)
    if not protected.startswith("|") or not protected.endswith("|"):
        raise MarkdownScriptError("表格行必须使用竖线包围")
    return [
        cell.strip().replace(_ESCAPED_PIPE, "|")
        for cell in protected[1:-1].split("|")
    ]


def _is_separator(cells: list[str]) -> bool:
    return len(cells) == 4 and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells
    )


def parse_markdown_script(markdown: str) -> MarkdownScriptDocument:
    try:
        markdown.encode("utf-8")
    except UnicodeError as exc:
        raise MarkdownScriptError("Markdown 必须使用 UTF-8 编码") from exc

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    titles = [match.group(1).strip() for line in lines if (match := re.fullmatch(r"#\s+(.+?)\s*", line))]
    if len(titles) != 1 or not titles[0]:
        raise MarkdownScriptError("缺少一级标题或一级标题数量不是 1")

    header_indexes = []
    for index, line in enumerate(lines):
        if line.strip().startswith("|"):
            cells = _split_table_row(line)
            if tuple(cells) == EXPECTED_COLUMNS:
                header_indexes.append(index)
    if len(header_indexes) != 1:
        raise MarkdownScriptError("缺少固定四列表格或表格数量不是 1")

    header_index = header_indexes[0]
    for line in lines[:header_index]:
        if line.strip() and not re.fullmatch(r"#\s+(.+?)\s*", line):
            raise MarkdownScriptError("Markdown 不能包含标题和表格之外的内容")
    if header_index + 1 >= len(lines) or not _is_separator(
        _split_table_row(lines[header_index + 1])
    ):
        raise MarkdownScriptError("表格分隔行无效")

    rows: list[MarkdownScriptRow] = []
    warnings: list[str] = []
    for source_line in lines[header_index + 2 :]:
        if not source_line.strip():
            continue
        cells = _split_table_row(source_line)
        if len(cells) != 4:
            raise MarkdownScriptError("表格必须恰好包含四列")
        number_text, text, emphasis_text, search_text = cells
        expected_number = len(rows) + 1
        if not number_text.isdigit() or int(number_text) != expected_number:
            raise MarkdownScriptError("序号必须从 1 开始连续递增")
        if not text:
            raise MarkdownScriptError(f"第 {expected_number} 行口播文案不能为空")
        emphasis_terms = _split_cell_list(
            emphasis_text, expected_number, "重点词", warnings
        )
        normalized_line = visible_text(text)
        for term in emphasis_terms:
            if visible_text(term) not in normalized_line:
                raise MarkdownScriptError(
                    f"第 {expected_number} 行重点词“{term}”不在口播文案中"
                )
        search_terms = _split_cell_list(
            search_text, expected_number, "素材搜索词", warnings
        )
        if len(search_terms) > 3:
            raise MarkdownScriptError(f"第 {expected_number} 行素材搜索词不能超过 3 个")
        for query in search_terms:
            if not _ENGLISH_QUERY.fullmatch(query):
                raise MarkdownScriptError(
                    f"第 {expected_number} 行素材搜索词必须使用英文"
                )
        if expected_number == 1 and not search_terms:
            raise MarkdownScriptError("第一行必须填写素材搜索词")
        rows.append(
            MarkdownScriptRow(
                number=expected_number,
                text=text,
                emphasis_terms=emphasis_terms,
                material_search_terms=search_terms,
            )
        )
    if not rows:
        raise MarkdownScriptError("口播表格不能为空")
    return MarkdownScriptDocument(title=titles[0], rows=rows, warnings=warnings)


def parse_markdown_upload(
    filename: str,
    payload: bytes,
) -> MarkdownScriptDocument:
    if Path(filename).suffix.lower() != ".md":
        raise MarkdownScriptError("上传文件必须使用 .md 扩展名")
    try:
        markdown = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MarkdownScriptError("Markdown 必须使用 UTF-8 编码") from exc
    return parse_markdown_script(markdown)


def build_scenes(document: MarkdownScriptDocument) -> list[ScriptScene]:
    scenes: list[ScriptScene] = []
    for row in document.rows:
        if row.material_search_terms:
            if scenes:
                previous = scenes[-1]
                scenes[-1] = ScriptScene(
                    index=previous.index,
                    first_row=previous.first_row,
                    last_row=row.number - 1,
                    search_terms=previous.search_terms,
                )
            scenes.append(
                ScriptScene(
                    index=len(scenes) + 1,
                    first_row=row.number,
                    last_row=row.number,
                    search_terms=tuple(row.material_search_terms),
                )
            )
    final = scenes[-1]
    scenes[-1] = ScriptScene(
        index=final.index,
        first_row=final.first_row,
        last_row=document.rows[-1].number,
        search_terms=final.search_terms,
    )
    return scenes
```

Create `examples/markdown-scripts/marriage.md` using the exact valid fixture from the test, expanded to at least six rows and three scenes.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_script_document.py
```

Expected: all parser tests pass.

- [ ] **Step 5: Commit the parser**

```powershell
git add app/services/script_document.py test/services/test_script_document.py examples/markdown-scripts/marriage.md
git commit -m "feat: parse markdown script tables"
```

## Task 3: WebUI upload, preview, clear, and restore

**Files:**
- Modify: `webui/Main.py:230-265, 830-1015, 1954-2110, 3251-3305`
- Modify: `webui/i18n/de.json`
- Modify: `webui/i18n/en.json`
- Modify: `webui/i18n/es.json`
- Modify: `webui/i18n/id.json`
- Modify: `webui/i18n/pt.json`
- Modify: `webui/i18n/ru.json`
- Modify: `webui/i18n/tr.json`
- Modify: `webui/i18n/vi.json`
- Modify: `webui/i18n/zh.json`
- Create: `test/services/test_webui_markdown_import.py`
- Modify: `test/services/test_subtitle_background_settings.py`

- [ ] **Step 1: Write failing WebUI helper and locale tests**

Create `test/services/test_webui_markdown_import.py`:

```python
from app.models.schema import VideoAspect, VideoParams
from app.services import script_document


def test_apply_document_sets_content_without_generation_settings():
    document = script_document.parse_markdown_script(
        """# 婚姻二字

| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
| 1 | 第一行文案。 | 第一行 | wedding couple |
| 2 | 第二行文案。 | | |
"""
    )
    params = VideoParams(
        video_subject="旧标题",
        voice_name="local:default",
        video_aspect=VideoAspect.landscape,
    )

    script_document.apply_to_video_params(document, params)

    assert params.video_subject == "婚姻二字"
    assert params.video_script == "第一行文案。\n第二行文案。"
    assert params.markdown_script == document
    assert params.voice_name == "local:default"
    assert params.video_aspect == VideoAspect.landscape
```

Also add these required keys to the existing locale-key assertion in `test/services/test_subtitle_background_settings.py`:

```python
"Import Markdown Script",
"Markdown Script Help",
"Clear Markdown Import",
"Markdown Parse Preview",
"Markdown Parse Error",
"Markdown Video Title",
"Markdown Script Rows",
"Markdown Scenes",
"Markdown Scene Search Terms",
"Markdown Requires Online Material Source",
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_webui_markdown_import.py test\services\test_subtitle_background_settings.py -k "markdown or all_locales"
```

Expected: FAIL because the parameter helper and locale keys do not exist.

- [ ] **Step 3: Add the pure parameter helper and Streamlit UI**

Add this function to `app/services/script_document.py`:

```python
def apply_to_video_params(document: MarkdownScriptDocument, params) -> None:
    params.video_subject = document.title
    params.video_script = document.script_text()
    params.video_terms = [
        term
        for scene in build_scenes(document)
        for term in scene.search_terms
    ]
    params.markdown_script = document
```

In `webui/Main.py`:

- Import `script_document`.
- Initialize `markdown_script_document`, `markdown_script_error`, and `markdown_script_hash` in session state.
- Add a `.md` `st.file_uploader` at the top of `_render_script_settings`.
- Pass the uploaded name and bytes to `parse_markdown_upload` only when the SHA-256 changes; this performs the `.md` and strict UTF-8 checks.
- Store only `document.model_dump(mode="json")` in session state.
- While a document is active, call `apply_to_video_params`, show title/row/scene metrics, every `document.warnings` entry, and two dataframes; disable the AI script/keyword buttons and text editors, and provide `Clear Markdown Import` to remove all three session keys.
- Set `markdown_script_error` and show `Markdown Parse Error` on decoding or parsing failure.
- Markdown scene search supports `pexels`, `pixabay`, and `coverr`. If an imported document is active while the source is `local`, show `Markdown Requires Online Material Source` and disable generation.
- Pass `disabled=bool(st.session_state.get("markdown_script_error")) or markdown_source_invalid` to the generation button.
- During `_apply_pending_task_restore`, restore `params["markdown_script"]` into `markdown_script_document` and do not attempt to repopulate the upload widget.

Use this normalized preview structure so the UI is not coupled to parser internals:

```python
document_rows = [
    {
        tr("Row"): row.number,
        tr("Video Script"): row.text,
        tr("Manual Emphasis Terms"): "；".join(row.emphasis_terms),
    }
    for row in document.rows
]
scene_rows = [
    {
        tr("Scene"): scene.index,
        tr("Rows"): f"{scene.first_row}-{scene.last_row}",
        tr("Markdown Scene Search Terms"): "；".join(scene.search_terms),
    }
    for scene in script_document.build_scenes(document)
]
```

Add translated values for all required keys to all nine locale JSON files. Chinese primary strings:

```json
{
  "Import Markdown Script": "导入口播稿 Markdown",
  "Markdown Script Help": "上传固定四列表格格式的 UTF-8 Markdown 文件。",
  "Clear Markdown Import": "清除 Markdown 导入",
  "Markdown Parse Preview": "Markdown 解析预览",
  "Markdown Parse Error": "Markdown 解析失败",
  "Markdown Video Title": "视频标题",
  "Markdown Script Rows": "口播行数",
  "Markdown Scenes": "场景数量",
  "Markdown Scene Search Terms": "场景素材搜索词",
  "Markdown Requires Online Material Source": "Markdown 场景搜索需要选择 Pexels、Pixabay 或 Coverr 素材源"
}
```

- [ ] **Step 4: Verify WebUI compilation and tests**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m compileall -q webui\Main.py app\services\script_document.py
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_webui_markdown_import.py test\services\test_subtitle_background_settings.py test\services\test_webui_i18n.py
```

Expected: compilation succeeds and all selected tests pass, including nine locale subtests.

- [ ] **Step 5: Commit WebUI import and preview**

```powershell
git add webui/Main.py webui/i18n test/services/test_webui_markdown_import.py test/services/test_subtitle_background_settings.py
git commit -m "feat: preview markdown scripts in webui"
```

## Task 4: Map document rows and scenes to subtitle time

**Files:**
- Modify: `app/services/script_document.py`
- Modify: `test/services/test_script_document.py`

- [ ] **Step 1: Write failing row-alignment tests**

Append to `test/services/test_script_document.py`:

```python
def test_align_rows_uses_sequential_occurrences_and_builds_scene_times():
    document = script_document.parse_markdown_script(
        """# 重复词测试

| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
| 1 | 婚姻需要沟通。 | 婚姻 | wedding couple |
| 2 | 婚姻也需要耐心。 | 婚姻 | couple talking |
"""
    )
    subtitles = [
        (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通"),
        (2, "00:00:02,000 --> 00:00:04,500", "婚姻也需要耐心"),
    ]

    timed_rows = script_document.align_rows_to_subtitles(document, subtitles)
    timed_scenes = script_document.build_timed_scenes(document, timed_rows)

    assert [(row.number, row.start, row.end) for row in timed_rows] == [
        (1, 0.0, 2.0),
        (2, 2.0, 4.5),
    ]
    assert [(scene.start, scene.end) for scene in timed_scenes] == [
        (0.0, 2.0),
        (2.0, 4.5),
    ]


def test_align_rows_reports_the_source_row_on_mismatch():
    document = script_document.parse_markdown_script(
        """# 对齐失败

| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
| 1 | 原始文案。 | 原始文案 | original text |
"""
    )

    with pytest.raises(script_document.MarkdownAlignmentError, match="第 1 行"):
        script_document.align_rows_to_subtitles(
            document,
            [(1, "00:00:00,000 --> 00:00:01,000", "完全不同")],
        )
```

- [ ] **Step 2: Run alignment tests and verify RED**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_script_document.py -k align
```

Expected: FAIL because timing models and alignment functions do not exist.

- [ ] **Step 3: Implement sequential alignment and timed scenes**

Add these runtime models and functions to `app/services/script_document.py`:

```python
@dataclass(frozen=True)
class TimedScriptRow:
    number: int
    text: str
    emphasis_terms: tuple[str, ...]
    start: float
    end: float


@dataclass(frozen=True)
class TimedScriptScene:
    index: int
    first_row: int
    last_row: int
    search_terms: tuple[str, ...]
    start: float
    end: float


class MarkdownAlignmentError(ValueError):
    pass


def _parse_srt_range(value: str) -> tuple[float, float]:
    def seconds(timestamp: str) -> float:
        hours, minutes, remainder = timestamp.strip().replace(",", ".").split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(remainder)

    start_text, end_text = value.split("-->", 1)
    return seconds(start_text), seconds(end_text)


def align_rows_to_subtitles(
    document: MarkdownScriptDocument,
    subtitles: list[tuple[int, str, str]],
) -> list[TimedScriptRow]:
    subtitle_spans = []
    joined = ""
    for subtitle_index, time_range, subtitle_text in subtitles:
        normalized = visible_text(subtitle_text)
        if not normalized:
            continue
        start, end = _parse_srt_range(time_range)
        span_start = len(joined)
        joined += normalized
        subtitle_spans.append((subtitle_index, span_start, len(joined), start, end))

    def time_for_offset(offset: int, use_end: bool) -> float:
        for _, span_start, span_end, start, end in subtitle_spans:
            if span_start <= offset < span_end:
                ratio = (offset - span_start + (1 if use_end else 0)) / (
                    span_end - span_start
                )
                return start + (end - start) * ratio
        raise MarkdownAlignmentError("字幕字符时间映射失败")

    cursor = 0
    timed_rows = []
    for row in document.rows:
        normalized = visible_text(row.text)
        start_offset = joined.find(normalized, cursor)
        if start_offset < 0:
            raise MarkdownAlignmentError(
                f"第 {row.number} 行无法映射到生成字幕：{row.text}"
            )
        end_offset = start_offset + len(normalized) - 1
        timed_rows.append(
            TimedScriptRow(
                number=row.number,
                text=row.text,
                emphasis_terms=tuple(row.emphasis_terms),
                start=time_for_offset(start_offset, False),
                end=time_for_offset(end_offset, True),
            )
        )
        cursor = end_offset + 1
    return timed_rows


def build_timed_scenes(
    document: MarkdownScriptDocument,
    timed_rows: list[TimedScriptRow],
) -> list[TimedScriptScene]:
    rows_by_number = {row.number: row for row in timed_rows}
    return [
        TimedScriptScene(
            index=scene.index,
            first_row=scene.first_row,
            last_row=scene.last_row,
            search_terms=scene.search_terms,
            start=rows_by_number[scene.first_row].start,
            end=rows_by_number[scene.last_row].end,
        )
        for scene in build_scenes(document)
    ]
```

- [ ] **Step 4: Run all script-document tests**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_script_document.py
```

Expected: all parser, validation, alignment, and scene tests pass.

- [ ] **Step 5: Commit row and scene timing**

```powershell
git add app/services/script_document.py test/services/test_script_document.py
git commit -m "feat: align markdown rows to subtitles"
```

## Task 5: Row-bound emphasis cues

**Files:**
- Modify: `app/services/emphasis.py:90-300`
- Modify: `test/services/test_emphasis.py`

- [ ] **Step 1: Write failing repeated-row emphasis tests**

Append to `test/services/test_emphasis.py`:

```python
from app.services.script_document import TimedScriptRow


def test_markdown_emphasis_repeats_the_same_term_in_each_source_row():
    rows = [
        TimedScriptRow(1, "婚姻需要沟通", ("婚姻",), 0.0, 2.0),
        TimedScriptRow(2, "婚姻也需要耐心", ("婚姻",), 2.0, 4.5),
    ]

    cues = emphasis.build_markdown_emphasis_cues("task-md", rows)

    assert [cue.text for cue in cues] == ["婚姻", "婚姻"]
    assert [cue.source_row_number for cue in cues] == [1, 2]
    assert cues[0].start == 0.0
    assert cues[1].start == 2.0


def test_markdown_emphasis_groups_multiple_terms_by_source_row():
    rows = [
        TimedScriptRow(
            1,
            "婚姻二字写起来只有两笔",
            ("婚姻二字", "只有两笔"),
            0.0,
            3.0,
        )
    ]

    cues = emphasis.build_markdown_emphasis_cues("task-md", rows)

    assert len(cues) == 2
    assert cues[0].layer == 0
    assert cues[1].layer == 1
    assert cues[0].end == cues[1].end == 3.0
```

- [ ] **Step 2: Run emphasis tests and verify RED**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_emphasis.py -k markdown
```

Expected: FAIL because row-bound cues and `source_row_number` do not exist.

- [ ] **Step 3: Implement row-bound cue generation**

Extend `EmphasisCue` with a backward-compatible field and include it in `to_dict`/`from_dict`:

```python
source_row_number: int = -1
```

Validate that it is `-1` or positive. Change `_arrange_grouped_cues` to group by source row when present:

```python
group_key = (
    cue.source_row_number
    if cue.source_row_number > 0
    else cue.subtitle_index
)
grouped.setdefault(group_key, []).append(cue)
```

Add the public builder:

```python
def build_markdown_emphasis_cues(
    task_id: str,
    timed_rows,
    random_colors: bool = True,
    random_animations: bool = True,
) -> list[EmphasisCue]:
    cues = []
    term_index = 0
    for row in timed_rows:
        visible_row = _visible_text(row.text)
        seconds_per_character = (row.end - row.start) / len(visible_row)
        for term in row.emphasis_terms:
            visible_term = _visible_text(term)
            start_index = visible_row.find(visible_term)
            if start_index < 0:
                raise ValueError(
                    f"第 {row.number} 行重点词“{term}”无法映射到字幕时间"
                )
            color, animation, sound_id, position = _style_for(
                task_id,
                row.number,
                term_index,
                random_colors=random_colors,
                random_animations=random_animations,
            )
            cues.append(
                EmphasisCue(
                    text=visible_term,
                    start=row.start + start_index * seconds_per_character,
                    end=row.end,
                    color=color,
                    animation=animation,
                    sound_id=sound_id,
                    position=position,
                    subtitle_index=-1,
                    source_row_number=row.number,
                )
            )
            term_index += 1
    return _arrange_grouped_cues(task_id, cues)
```

- [ ] **Step 4: Run all emphasis tests**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_emphasis.py test\services\test_video.py -k emphasis
```

Expected: all legacy and Markdown emphasis tests pass.

- [ ] **Step 5: Commit row-bound emphasis**

```powershell
git add app/services/emphasis.py test/services/test_emphasis.py
git commit -m "feat: bind emphasis cues to markdown rows"
```

## Task 6: Scene-aware material acquisition with fallback queries

**Files:**
- Modify: `app/services/material.py:330-480`
- Modify: `test/services/test_material.py`

- [ ] **Step 1: Write failing fallback and coverage tests**

Append to `test/services/test_material.py`:

```python
from app.models.schema import MaterialInfo, VideoAspect
from app.services.script_document import TimedScriptScene


def test_download_scene_materials_tries_fallbacks_and_covers_each_scene(tmp_path):
    scenes = [
        TimedScriptScene(1, 1, 2, ("first query", "fallback query"), 0.0, 6.0),
        TimedScriptScene(2, 3, 3, ("second scene",), 6.0, 9.0),
    ]
    result_map = {
        "first query": [],
        "fallback query": [
            MaterialInfo(provider="pixabay", url="https://x/a.mp4", duration=4),
            MaterialInfo(provider="pixabay", url="https://x/b.mp4", duration=4),
        ],
        "second scene": [
            MaterialInfo(provider="pixabay", url="https://x/c.mp4", duration=5)
        ],
    }

    with (
        patch.object(material, "search_videos_pixabay", side_effect=lambda search_term, **_: result_map[search_term]) as search,
        patch.object(material, "save_video", side_effect=lambda url, save_dir: str(tmp_path / Path(url).name)),
    ):
        plans = material.download_scene_materials(
            task_id="scene-task",
            scenes=scenes,
            source="pixabay",
            video_aspect=VideoAspect.landscape,
            max_clip_duration=5,
        )

    assert [call.kwargs["search_term"] for call in search.call_args_list] == [
        "first query",
        "fallback query",
        "second scene",
    ]
    assert [plan.scene_index for plan in plans] == [1, 2]
    assert len(plans[0].video_paths) == 2
    assert plans[0].required_duration == 6.0


def test_pixabay_search_log_never_contains_the_api_key():
    config.app["pixabay_api_keys"] = ["secret-pixabay-key"]
    fake_response = SimpleNamespace(json=lambda: {"hits": []})

    with (
        patch.object(material.requests, "get", return_value=fake_response),
        patch.object(material.logger, "info") as info,
    ):
        material.search_videos_pixabay(
            "wedding couple",
            minimum_duration=1,
            video_aspect=VideoAspect.landscape,
        )

    assert "secret-pixabay-key" not in "\n".join(
        str(call) for call in info.call_args_list
    )
```

Add a second test where every query returns an empty list and assert `SceneMaterialError` contains the scene number, row range, and attempted terms.

- [ ] **Step 2: Run scene-material tests and verify RED**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_material.py -k scene_material
```

Expected: FAIL because scene material types and downloader do not exist.

- [ ] **Step 3: Implement scene acquisition**

Add to `app/services/material.py`:

```python
from dataclasses import dataclass
from pathlib import Path

from app.services.script_document import TimedScriptScene


class SceneMaterialError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedSceneMaterials:
    scene_index: int
    first_row: int
    last_row: int
    start: float
    end: float
    required_duration: float
    search_terms: tuple[str, ...]
    video_paths: tuple[str, ...]


def _search_function(source: str):
    functions = {
        "pexels": search_videos_pexels,
        "pixabay": search_videos_pixabay,
        "coverr": search_videos_coverr,
    }
    if source not in functions:
        raise SceneMaterialError(f"Markdown 场景不支持素材源：{source}")
    return functions[source]


def _scene_material_directory(task_id: str) -> str:
    material_directory = config.app.get("material_directory", "").strip()
    if material_directory == "task":
        return utils.task_dir(task_id)
    if material_directory and not os.path.isdir(material_directory):
        return ""
    return material_directory


def download_scene_materials(
    task_id: str,
    scenes: list[TimedScriptScene],
    source: str,
    video_aspect: VideoAspect,
    max_clip_duration: int,
) -> list[DownloadedSceneMaterials]:
    search = _search_function(source)
    save_dir = _scene_material_directory(task_id)
    plans = []
    used_urls = set()
    for scene in scenes:
        required = scene.end - scene.start
        covered = 0.0
        paths = []
        for query in scene.search_terms:
            candidates = search(
                search_term=query,
                minimum_duration=max(1, min(max_clip_duration, int(required + 0.999))),
                video_aspect=video_aspect,
            )
            for item in candidates:
                if item.url in used_urls:
                    continue
                saved = save_video(item.url, save_dir=save_dir)
                if not saved:
                    continue
                used_urls.add(item.url)
                paths.append(saved)
                covered += min(float(item.duration), float(max_clip_duration))
                if covered >= required:
                    break
            if covered >= required:
                break
        if covered < required:
            terms = "；".join(scene.search_terms)
            raise SceneMaterialError(
                f"场景 {scene.index} 素材搜索失败：{terms}；对应文案："
                f"第 {scene.first_row}～{scene.last_row} 行"
            )
        plans.append(
            DownloadedSceneMaterials(
                scene_index=scene.index,
                first_row=scene.first_row,
                last_row=scene.last_row,
                start=scene.start,
                end=scene.end,
                required_duration=required,
                search_terms=scene.search_terms,
                video_paths=tuple(paths),
            )
        )
    return plans
```

Use the same task/cache-directory normalization already used by `download_videos`. In `search_videos_pixabay`, replace the log of `query_url` with a key-free message:

```python
logger.info(
    f"searching Pixabay videos: query={search_term}, with proxies: {config.proxy}"
)
```

The request still uses `query_url`; only the log changes.

- [ ] **Step 4: Run material tests**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_material.py
```

Expected: all existing provider/aspect tests and new scene tests pass.

- [ ] **Step 5: Commit scene acquisition**

```powershell
git add app/services/material.py test/services/test_material.py
git commit -m "feat: download materials by markdown scene"
```

## Task 7: Scene-bounded video composition

**Files:**
- Modify: `app/services/video.py:532-756`
- Modify: `test/services/test_video.py`

- [ ] **Step 1: Write failing allocation and dispatch tests**

Extend the existing schema import in `test/services/test_video.py`:

```python
from app.models.schema import MaterialInfo, VideoAspect, VideoTransitionMode
```

Add these module-level pytest tests:

```python

def test_allocate_scene_clips_never_crosses_scene_duration():
    allocations = vd.allocate_scene_clips(
        video_paths=("a.mp4", "b.mp4"),
        source_durations={"a.mp4": 5.0, "b.mp4": 5.0},
        required_duration=6.5,
        max_clip_duration=5,
    )

    assert [(item.video_path, item.duration) for item in allocations] == [
        ("a.mp4", 5.0),
        ("b.mp4", 1.5),
    ]
    assert sum(item.duration for item in allocations) == 6.5


def test_combine_scene_videos_concatenates_in_scene_order():
    plans = [
        types.SimpleNamespace(scene_index=1, required_duration=2.0, video_paths=("a.mp4",)),
        types.SimpleNamespace(scene_index=2, required_duration=3.0, video_paths=("b.mp4",)),
    ]
    with (
        patch.object(vd, "_probe_video_duration", side_effect={"a.mp4": 4.0, "b.mp4": 4.0}.get),
        patch.object(vd, "_render_scene_allocation", side_effect=["scene-1.mp4", "scene-2.mp4"]),
        patch.object(vd, "concat_video_clips_with_ffmpeg") as concat,
    ):
        vd.combine_scene_videos(
            "combined.mp4",
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert concat.call_args.kwargs["clip_files"] == ["scene-1.mp4", "scene-2.mp4"]
    assert concat.call_args.kwargs["max_duration"] == 5.0
```

- [ ] **Step 2: Run scene-video tests and verify RED**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_video.py -k scene
```

Expected: FAIL because allocation and scene composer functions do not exist.

- [ ] **Step 3: Implement exact scene allocation and composition**

Add a small immutable allocation type and pure allocator to `app/services/video.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SceneClipAllocation:
    video_path: str
    duration: float


def allocate_scene_clips(
    video_paths: tuple[str, ...],
    source_durations: dict[str, float],
    required_duration: float,
    max_clip_duration: int,
) -> list[SceneClipAllocation]:
    remaining = required_duration
    allocations = []
    for video_path in video_paths:
        if remaining <= 0:
            break
        usable = min(source_durations[video_path], float(max_clip_duration), remaining)
        if usable > 0:
            allocations.append(SceneClipAllocation(video_path, usable))
            remaining -= usable
    if remaining > 0.001:
        raise ValueError(f"scene material is {remaining:.3f}s short")
    return allocations
```

Add the duration probe and focused rendering helpers below. They deliberately reuse the same MoviePy effects, codec fallback, FFmpeg concatenation, and `close_clip` cleanup primitives as `combine_videos`:

```python
def _probe_video_duration(video_path: str) -> float:
    clip = _open_video_clip_quietly(video_path)
    try:
        return float(clip.duration)
    finally:
        close_clip(clip)


def _fit_scene_clip(clip, video_width: int, video_height: int):
    if clip.size == [video_width, video_height] or clip.size == (
        video_width,
        video_height,
    ):
        return clip
    clip_ratio = clip.w / clip.h
    target_ratio = video_width / video_height
    if abs(clip_ratio - target_ratio) < 0.0001:
        return clip.resized(new_size=(video_width, video_height))
    scale = video_width / clip.w if clip_ratio > target_ratio else video_height / clip.h
    resized = clip.resized(new_size=(int(clip.w * scale), int(clip.h * scale)))
    background = ColorClip(
        size=(video_width, video_height), color=(0, 0, 0)
    ).with_duration(resized.duration)
    return CompositeVideoClip([background, resized.with_position("center")])


def _apply_scene_transition(clip, transition_mode):
    value = getattr(transition_mode, "value", transition_mode)
    if value in (None, VideoTransitionMode.none.value):
        return clip
    duration = min(1.0, max(0.01, clip.duration / 2))
    side = random.choice(["left", "right", "top", "bottom"])
    effects = {
        VideoTransitionMode.fade_in.value: lambda value_clip: video_effects.fadein_transition(value_clip, duration),
        VideoTransitionMode.fade_out.value: lambda value_clip: video_effects.fadeout_transition(value_clip, duration),
        VideoTransitionMode.slide_in.value: lambda value_clip: video_effects.slidein_transition(value_clip, duration, side),
        VideoTransitionMode.slide_out.value: lambda value_clip: video_effects.slideout_transition(value_clip, duration, side),
        VideoTransitionMode.zoom_in.value: lambda value_clip: video_effects.zoomin_transition(value_clip, duration),
        VideoTransitionMode.zoom_out.value: lambda value_clip: video_effects.zoomout_transition(value_clip, duration),
    }
    if value == VideoTransitionMode.shuffle.value:
        return random.choice(list(effects.values()))(clip)
    if value not in effects:
        raise ValueError(f"unsupported video transition: {value}")
    return effects[value](clip)


def _render_scene_allocation(
    output_dir: str,
    scene_index: int,
    allocations: list[SceneClipAllocation],
    video_aspect,
    video_transition_mode,
    threads: int,
    clip_speed: float,
) -> str:
    width, height = VideoAspect(video_aspect).to_resolution()
    speed = utils.normalize_clip_speed(clip_speed)
    allocation_files = []
    for allocation_index, allocation in enumerate(allocations, start=1):
        clip = _open_video_clip_quietly(allocation.video_path)
        try:
            source_duration = min(
                float(clip.duration), allocation.duration * speed
            )
            clip = clip.subclipped(0, source_duration)
            if speed != 1.0:
                clip = clip.with_speed_scaled(speed)
            if clip.duration > allocation.duration:
                clip = clip.subclipped(0, allocation.duration)
            clip = _fit_scene_clip(clip, width, height)
            clip = _apply_scene_transition(clip, video_transition_mode)
            clip_file = os.path.join(
                output_dir,
                f"scene-{scene_index}-clip-{allocation_index}.mp4",
            )
            _write_videofile_with_codec_fallback(
                clip,
                clip_file,
                codec=_get_configured_video_codec(),
                logger=None,
                fps=fps,
            )
            allocation_files.append(clip_file)
        finally:
            close_clip(clip)
    scene_file = os.path.join(output_dir, f"scene-{scene_index}.mp4")
    scene_duration = sum(item.duration for item in allocations)
    concat_video_clips_with_ffmpeg(
        clip_files=allocation_files,
        output_file=scene_file,
        threads=threads,
        output_dir=output_dir,
        max_duration=scene_duration,
    )
    delete_files(allocation_files)
    return scene_file
```

Then add the scene-level dispatcher:

```python
def combine_scene_videos(
    combined_video_path,
    scene_plans,
    video_aspect,
    video_transition_mode,
    max_clip_duration,
    threads,
    clip_speed,
):
    output_dir = os.path.dirname(combined_video_path)
    scene_files = []
    total_duration = 0.0
    normalized_speed = utils.normalize_clip_speed(clip_speed)
    for plan in sorted(scene_plans, key=lambda item: item.scene_index):
        durations = {
            video_path: _probe_video_duration(video_path) / normalized_speed
            for video_path in plan.video_paths
        }
        allocations = allocate_scene_clips(
            plan.video_paths,
            durations,
            plan.required_duration,
            max_clip_duration,
        )
        scene_file = _render_scene_allocation(
            output_dir=output_dir,
            scene_index=plan.scene_index,
            allocations=allocations,
            video_aspect=video_aspect,
            video_transition_mode=video_transition_mode,
            threads=threads,
            clip_speed=normalized_speed,
        )
        scene_files.append(scene_file)
        total_duration += plan.required_duration
    concat_video_clips_with_ffmpeg(
        clip_files=scene_files,
        output_file=combined_video_path,
        threads=threads,
        output_dir=output_dir,
        max_duration=total_duration,
    )
    delete_files(scene_files)
    return combined_video_path
```

The final allocation of every scene is trimmed to its exact remaining duration. No previous scene is looped into the next scene.

- [ ] **Step 4: Run video tests**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_video.py
```

Expected: all legacy composition, subtitle, emphasis, and new scene tests pass.

- [ ] **Step 5: Commit scene composition**

```powershell
git add app/services/video.py test/services/test_video.py
git commit -m "feat: compose videos within markdown scenes"
```

## Task 8: Markdown task orchestration without LLM calls

**Files:**
- Modify: `app/services/task.py:15-90, 318-440, 445-600`
- Modify: `test/services/test_task.py`
- Modify: `test/services/test_webui_task_history.py`

- [ ] **Step 1: Write a failing no-LLM orchestration test**

Add to `test/services/test_task.py`:

```python
def test_markdown_task_bypasses_all_llm_generation_and_uses_scene_pipeline(self):
    params = VideoParams(
        video_subject="婚姻二字",
        video_source="pixabay",
        emphasis_enabled=True,
        markdown_script={
            "title": "婚姻二字",
            "rows": [
                {
                    "number": 1,
                    "text": "婚姻需要沟通。",
                    "emphasis_terms": ["婚姻"],
                    "material_search_terms": ["wedding couple"],
                },
                {
                    "number": 2,
                    "text": "婚姻也需要耐心。",
                    "emphasis_terms": ["婚姻"],
                    "material_search_terms": [],
                },
            ],
        },
    )
    subtitles = [
        (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通"),
        (2, "00:00:02,000 --> 00:00:04,000", "婚姻也需要耐心"),
    ]
    with tempfile.TemporaryDirectory() as task_dir, (
        patch.object(tm.utils, "task_dir", return_value=task_dir),
        patch.object(tm.llm, "generate_script", side_effect=AssertionError("LLM script called")),
        patch.object(tm.llm, "generate_terms", side_effect=AssertionError("LLM terms called")),
        patch.object(tm.llm, "generate_emphasis_terms", side_effect=AssertionError("LLM emphasis called")),
        patch.object(tm.llm, "generate_social_metadata", side_effect=AssertionError("LLM metadata called")),
        patch.object(tm, "generate_audio", return_value=("audio.wav", 4.0, object())),
        patch.object(tm, "generate_subtitle", return_value=str(Path(task_dir) / "subtitle.srt")),
        patch.object(tm.subtitle, "file_to_subtitles", return_value=subtitles),
        patch.object(tm.material, "download_scene_materials", return_value=[SimpleNamespace(scene_index=1, required_duration=4.0, video_paths=("scene.mp4",))]) as download,
        patch.object(tm, "generate_final_videos", return_value=(["final.mp4"], ["combined.mp4"])) as final,
        patch.object(tm.upload_post.upload_post_service, "is_configured", return_value=True),
        patch.object(tm.upload_post.upload_post_service, "auto_upload", True),
        patch.object(tm.upload_post.upload_post_service, "platforms", ["youtube"]),
        patch.object(tm.upload_post, "cross_post_video", return_value={"success": True}),
        patch.object(tm.sm.state, "update_task"),
    ):
        result = tm.start("markdown-task", params)

    assert result["script"] == "婚姻需要沟通。\n婚姻也需要耐心。"
    assert download.called
    assert final.call_args.kwargs["scene_materials"]
```

Add a history test asserting `VideoParams.model_validate` restores `markdown_script` from `script.json` and the WebUI restore payload retains it.

- [ ] **Step 2: Run task tests and verify RED**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_task.py test\services\test_webui_task_history.py -k markdown
```

Expected: FAIL because `task.start` still generates global terms and does not pass scene materials.

- [ ] **Step 3: Route Markdown tasks through the structured pipeline**

Implement these branches in `app/services/task.py`:

```python
def _markdown_runtime(params, subtitle_path):
    document = params.markdown_script
    if document is None:
        return None
    subtitles = subtitle.file_to_subtitles(subtitle_path)
    timed_rows = script_document.align_rows_to_subtitles(document, subtitles)
    timed_scenes = script_document.build_timed_scenes(document, timed_rows)
    return document, timed_rows, timed_scenes
```

At task start:

```python
if params.markdown_script is not None:
    video_script = params.markdown_script.script_text()
    params.video_subject = params.markdown_script.title
    params.video_script = video_script
    video_terms = [
        term
        for scene in script_document.build_scenes(params.markdown_script)
        for term in scene.search_terms
    ]
else:
    video_script = generate_script(task_id, params)
    video_terms = "" if params.video_source == "local" else generate_terms(
        task_id, params, video_script
    )
```

After subtitle creation, build Markdown timing once. Use `build_markdown_emphasis_cues` and write the existing `emphasis.json`; do not call `generate_emphasis_terms`. For online material sources call `download_scene_materials`. Pass `scene_materials` to `generate_final_videos`, which dispatches to `video.combine_scene_videos`; the legacy path continues to call `video.combine_videos`.

For compatibility with task results and history, also derive the flat path list without losing the structured plans:

```python
downloaded_videos = [
    video_path
    for scene_item in scene_materials
    for video_path in scene_item.video_paths
]
```

Update the final-video function signature and dispatch:

```python
def generate_final_videos(
    task_id,
    params,
    downloaded_videos,
    audio_file,
    subtitle_path,
    emphasis_path="",
    scene_materials=None,
):
    if scene_materials:
        video.combine_scene_videos(
            combined_video_path=combined_video_path,
            scene_plans=scene_materials,
            video_aspect=params.video_aspect,
            video_transition_mode=params.video_transition_mode,
            max_clip_duration=params.video_clip_duration,
            threads=params.n_threads,
            clip_speed=params.video_clip_speed,
        )
    else:
        video.combine_videos(
            combined_video_path=combined_video_path,
            video_paths=downloaded_videos,
            audio_file=audio_file,
            video_aspect=params.video_aspect,
            video_concat_mode=video_concat_mode,
            video_transition_mode=params.video_transition_mode,
            max_clip_duration=params.video_clip_duration,
            threads=params.n_threads,
            clip_speed=params.video_clip_speed,
        )
```

Persist normalized Markdown parameters in `script.json`. After scene downloads, write `scene-materials.json` with this exact shape:

```python
scene_manifest_path = path.join(utils.task_dir(task_id), "scene-materials.json")
with open(scene_manifest_path, "w", encoding="utf-8") as manifest_file:
    manifest_file.write(
        utils.to_json(
            [
                {
                    "scene_index": item.scene_index,
                    "first_row": item.first_row,
                    "last_row": item.last_row,
                    "start": item.start,
                    "end": item.end,
                    "required_duration": item.required_duration,
                    "search_terms": list(item.search_terms),
                    "video_paths": list(item.video_paths),
                }
                for item in scene_materials
            ]
        )
    )
```

For Markdown tasks with automatic cross-post enabled, use the Markdown title and an empty metadata payload; do not call `llm.generate_social_metadata`:

```python
if params.markdown_script is not None:
    youtube_extra = {
        "youtube_title": params.markdown_script.title,
        "youtube_description": "",
        "tags": [],
        "privacyStatus": upload_post.upload_post_service.youtube_privacy_status,
        "containsSyntheticMedia": True,
    }
else:
    metadata = llm.generate_social_metadata(
        video_subject=params.video_subject,
        video_script=video_script,
        language=params.video_language or "",
        platform="youtube_shorts",
    )
    youtube_extra = {
        "youtube_title": metadata.get("title", params.video_subject),
        "youtube_description": metadata.get("caption", ""),
        "tags": metadata.get("hashtags", []),
        "privacyStatus": upload_post.upload_post_service.youtube_privacy_status,
        "containsSyntheticMedia": True,
    }
```

- [ ] **Step 4: Run focused orchestration and compatibility tests**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_task.py test\services\test_webui_task_history.py test\services\test_emphasis.py test\services\test_material.py test\services\test_video.py
```

Expected: Markdown no-LLM test and all legacy workflow tests pass.

- [ ] **Step 5: Commit the integrated task route**

```powershell
git add app/services/task.py test/services/test_task.py test/services/test_webui_task_history.py
git commit -m "feat: run markdown video tasks without llm"
```

## Task 9: Windows-native sample and complete verification

**Files:**
- Create: `scripts/generate_markdown_marriage_video.py`
- Modify: `docs/superpowers/specs/2026-07-15-markdown-script-import-design.md` only if implementation reveals a confirmed behavior change
- Modify: `task_plan.md`, `findings.md`, `progress.md` as local progress artifacts; do not stage them unless explicitly requested

- [ ] **Step 1: Create a deterministic sample runner**

Create `scripts/generate_markdown_marriage_video.py` that:

```python
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.models.schema import VideoAspect, VideoParams
from app.services import script_document, task


MARKDOWN_FILE = PROJECT_ROOT / "examples" / "markdown-scripts" / "marriage.md"
TASK_ID = "marriage-markdown-landscape-test"


def main() -> int:
    document = script_document.parse_markdown_script(
        MARKDOWN_FILE.read_text(encoding="utf-8")
    )
    params = VideoParams(
        video_subject=document.title,
        video_script=document.script_text(),
        markdown_script=document,
        video_source="pixabay",
        video_aspect=VideoAspect.landscape,
        voice_name="local:default",
        subtitle_enabled=True,
        emphasis_enabled=True,
        emphasis_font_name="SimHei.ttf",
        emphasis_sfx_enabled=True,
        emphasis_sfx_volume=0.5,
    )
    result = task.start(TASK_ID, params)
    if not result or not result.get("videos"):
        raise RuntimeError("Markdown sample did not produce a final video")
    print(f"final={result['videos'][0]}")
    print(f"emphasis={result['emphasis_path']}")
    print(f"materials={len(result['materials'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Keep all execution in the bundled Windows Python environment. Reuse previously downloaded sample audio/materials through explicit environment flags only if the runner validates that the reusable files match the current document.

- [ ] **Step 2: Run focused tests before the expensive sample**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q test\services\test_script_document.py test\services\test_schema.py test\services\test_emphasis.py test\services\test_material.py test\services\test_video.py test\services\test_task.py test\services\test_webui_markdown_import.py
```

Expected: all focused tests pass.

- [ ] **Step 3: Generate and inspect the real landscape sample**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe scripts\generate_markdown_marriage_video.py
```

Then verify media metadata:

```powershell
ffprobe -v error -show_entries format=duration,size -show_entries stream=index,codec_type,codec_name,width,height,r_frame_rate -of json storage\tasks\marriage-markdown-landscape-test\final-1.mp4
```

Expected: H.264 1920×1080 at 30 fps, an AAC audio stream, non-zero duration, and non-zero size.

Read `scene-materials.json` and `emphasis.json`. Extract one frame inside each scene and frames where repeated and simultaneous emphasis terms are active:

```powershell
ffmpeg -y -v error -ss 2.20 -i storage\tasks\marriage-markdown-landscape-test\final-1.mp4 -frames:v 1 storage\tasks\marriage-markdown-landscape-test\preview-scene-01.png
ffmpeg -y -v error -ss 7.20 -i storage\tasks\marriage-markdown-landscape-test\final-1.mp4 -frames:v 1 storage\tasks\marriage-markdown-landscape-test\preview-scene-02.png
```

Choose exact extraction times from the generated manifests, not from assumptions. Inspect the PNG files with the image viewer and confirm scene boundaries, bottom subtitles, SimHei glyph padding, multi-term layers, and visible emphasis sound entries.

- [ ] **Step 4: Run full regression and repository checks**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m compileall -q app webui scripts
.\tools\miniforge3\envs\mpt\python.exe -m pytest -q --ignore=third_party
git diff --check
git status --short
```

Expected: compilation succeeds; the full suite passes with only known deprecation warnings; `git diff --check` reports no whitespace errors; pre-existing unrelated worktree changes remain unstaged.

- [ ] **Step 5: Commit the acceptance example**

```powershell
git add scripts/generate_markdown_marriage_video.py
git commit -m "test: add markdown video acceptance sample"
```

## Final acceptance checklist

- [ ] A valid UTF-8 `.md` file parses into the exact title, rows, emphasis terms, and scenes shown in preview.
- [ ] Invalid title, columns, numbering, row text, emphasis terms, first-scene query, non-English query, and excess fallback queries block generation with row-specific messages.
- [ ] Clearing the import returns WebUI to the existing manual/AI workflow.
- [ ] Restoring task history recreates the structured preview without trying to repopulate the browser upload control.
- [ ] Repeated emphasis text in different rows generates a cue in every marked row.
- [ ] Empty material-search cells inherit the previous scene.
- [ ] Fallback queries are tried in file order and each scene downloads enough duration.
- [ ] Scene composition trims at exact scene boundaries and never loops an earlier scene into a later one.
- [ ] Markdown task generation does not call script, material-term, emphasis-term, or social-metadata LLM functions.
- [ ] Legacy manual, automatic, local-material, and old-task paths remain green.
- [ ] The Windows-native real sample has correct video/audio metadata and visually verified subtitles, emphasis, and scene changes.
