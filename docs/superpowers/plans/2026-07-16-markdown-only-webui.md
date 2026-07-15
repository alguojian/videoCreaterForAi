# Markdown-only WebUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the WebUI accept a final Markdown script as the only content source and remove all visible LLM configuration and generation controls while preserving the existing Markdown task pipeline.

**Architecture:** Keep `app.services.task` and the existing LLM backend/API for compatibility, but remove their WebUI entry points. `webui/Main.py` will render one Markdown upload/preview flow, populate `VideoParams` from the parsed document, and refuse to start a task until a valid Markdown document is loaded.

**Tech Stack:** Python 3.11, Streamlit, AST-based WebUI source tests, pytest, Windows PowerShell

---

## File map

- Modify `test/services/test_webui_markdown_import.py`: add source-level regression assertions for the Markdown-only UI and the no-upload generation guard.
- Modify `webui/Main.py`: remove visible LLM settings and script-generation controls, simplify session restore/onboarding, retain Markdown preview, and require a parsed Markdown document before generation.
- Keep `app/services/task.py`: its existing `markdown_document` branch already bypasses `generate_script`, `generate_terms`, and automatic emphasis-term generation; existing task tests remain the runtime proof.
- Keep `app/models/schema.py` and `app/controllers/v1/llm.py`: backend compatibility is explicitly outside this UI change.

### Task 1: Add failing Markdown-only UI tests

**Files:**
- Modify: `test/services/test_webui_markdown_import.py`
- Test source: `webui/Main.py`

- [ ] **Step 1: Add a test that rejects the old LLM and manual script controls**

Append a test that reads the AST function sources and asserts the settings dialog and script panel no longer contain the old UI entry points:

```python
def test_webui_is_markdown_only_without_llm_controls():
    source = WEBUI_MAIN.read_text(encoding="utf-8")
    settings = _function_source("_render_settings_dialog")
    script_settings = _function_source("_render_script_settings")

    for forbidden in (
        "LLM Settings Tab",
        "LLM Provider",
        "Test LLM Connection",
        "Generate Video Script and Keywords",
        "Generate Video Keywords",
        "Advanced Script Settings",
        "Video Subject Placeholder",
        "llm.generate_script",
        "llm.generate_terms",
    ):
        assert forbidden not in source
        assert forbidden not in settings
        assert forbidden not in script_settings

    assert 'tr("Import Markdown Script")' in script_settings
    assert 'tr("Markdown Parse Preview")' in script_settings
    assert 'tr("Markdown Script Help")' in script_settings
    assert "apply_to_video_params" in script_settings
```

- [ ] **Step 2: Add a test that requires a Markdown document before generation**

Append a test for `_render_generation_controls`:

```python
def test_generation_controls_require_markdown_document():
    generation_controls = _function_source("_render_generation_controls")

    assert "not params.markdown_script" in generation_controls
    assert "markdown_script_error" in generation_controls
    assert 'tr("Generate Video")' in generation_controls
```

- [ ] **Step 3: Run the focused tests and verify the new tests fail for the intended reason**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_webui_markdown_import.py -q
```

Expected: existing tests pass, and the two new tests fail because the current `Main.py` still contains the LLM settings and generation controls and does not require `params.markdown_script`.

- [ ] **Step 4: Commit the failing tests**

```powershell
git add test/services/test_webui_markdown_import.py
git commit -m "test: require markdown-only webui"
```

### Task 2: Remove visible LLM workflows and enforce Markdown-only generation

**Files:**
- Modify: `webui/Main.py`

- [ ] **Step 1: Remove LLM-only imports, helper functions, and session UI state**

Remove the `app.models.llm_provider` import block, the `llm` import from `app.services`, and helpers used only by the removed settings/prompt controls: `get_llm_provider_tips`, `get_llm_provider_label`, `reset_script_system_prompt`, `render_script_prompt_preview`, and `get_groq_model_ids`.

Remove `video_script_prompt` and `custom_system_prompt` from the WebUI defaults and task-restore assignments. Keep `video_subject`, `video_script`, `video_terms`, and serialized `markdown_script` because the existing task history and Markdown preview use them internally.

- [ ] **Step 2: Replace the settings dialog tabs**

Change `_render_settings_dialog` from four tabs to three tabs:

```python
right_config_panel, cache_config_panel, left_config_panel = st.tabs(
    [
        tr("Material API Tab"),
        tr("Cache Management Tab"),
        tr("Interface Settings Tab"),
    ]
)
```

Keep the existing material API, cache, and interface rendering. Remove the entire LLM provider form, model list lookup, provider tips, API key fields, and connection test button.

- [ ] **Step 3: Make the script panel upload/preview-only**

Retain Markdown upload parsing, error display, clear button, title/row/scene metrics, row dataframe, scene dataframe, and `script_document.apply_to_video_params`.

Delete the subject input, language selector, advanced script settings, script-generation button, editable script textarea, keyword-generation button, and editable keyword textarea. When no document is loaded, show the existing localized `tr("Markdown Script Help")` in an info message so the user knows to upload the required file.

After a successful upload, keep the parsed values in `params` and session state for task creation; do not call any `llm.*` function from `Main.py`.

- [ ] **Step 4: Remove the LLM-oriented onboarding step**

Update `render_onboarding_tour` so its first step targets `main_settings_grid` and describes Markdown upload using `tr("Import Markdown Script")` and `tr("Markdown Script Help")`. Keep the video settings and generate-video steps, but do not reference model settings, topic input, or LLM configuration.

- [ ] **Step 5: Require a valid Markdown document before enabling generation**

In `_render_generation_controls`, extend the existing `generation_disabled` expression:

```python
generation_disabled = bool(
    st.session_state.get("markdown_script_error")
) or markdown_source_invalid or not params.markdown_script
```

Keep the existing online-material-source validation. The generate button must be disabled until a valid document has been parsed, so an empty subject or manually entered script can no longer start the workflow.

- [ ] **Step 6: Run the focused tests and confirm they pass**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest test/services/test_webui_markdown_import.py -q
```

Expected: all focused WebUI Markdown tests pass.

- [ ] **Step 7: Commit the WebUI implementation**

```powershell
git add webui/Main.py
git commit -m "feat: make webui markdown-only"
```

### Task 3: Verify the end-to-end Markdown/no-LLM contract

**Files:**
- Verify: `webui/Main.py`
- Verify: `test/services/test_webui_markdown_import.py`
- Verify: `test/services/test_task.py`
- Verify: `test/services/test_script_document.py`

- [ ] **Step 1: Confirm no visible LLM workflow remains in the WebUI source**

Run:

```powershell
$source = Get-Content -LiteralPath webui\Main.py -Raw
foreach ($text in @(
  'LLM Settings Tab',
  'Test LLM Connection',
  'Generate Video Script and Keywords',
  'Generate Video Keywords',
  'llm.generate_script',
  'llm.generate_terms'
)) { if ($source.Contains($text)) { throw "Legacy LLM UI remains: $text" } }
```

Expected: the command exits successfully with no output.

- [ ] **Step 2: Run all relevant regression tests**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -m pytest `
  test/services/test_webui_markdown_import.py `
  test/services/test_script_document.py `
  test/services/test_task.py -q
```

Expected: all tests pass, including the existing Markdown task tests that patch every LLM function to fail if called.

- [ ] **Step 3: Import the WebUI module and parse the published sample**

Run:

```powershell
.\tools\miniforge3\envs\mpt\python.exe -c "import ast; from pathlib import Path; from app.services.script_document import parse_markdown_script; ast.parse(Path('webui/Main.py').read_text(encoding='utf-8')); d=parse_markdown_script(Path('examples/markdown-scripts/marriage.md').read_text(encoding='utf-8')); print(f'rows={len(d.rows)}, warnings={len(d.warnings)}')"
```

Expected: AST parsing succeeds and output is `rows=6, warnings=0`.

- [ ] **Step 4: Check formatting and repository state**

Run:

```powershell
git diff --check HEAD~2..HEAD
git status --short
```

Expected: no diff-check output and only intentional implementation commits are present.

- [ ] **Step 5: Push the current Master branch after final verification**

Run:

```powershell
git push origin Master
git rev-parse HEAD
git ls-remote --heads origin Master
```

Expected: local HEAD and remote `Master` report the same SHA.
