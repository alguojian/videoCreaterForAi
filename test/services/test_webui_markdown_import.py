import ast
from pathlib import Path

from app.models.schema import VideoAspect, VideoParams
from app.services import script_document


ROOT_DIR = Path(__file__).parent.parent.parent
WEBUI_MAIN = ROOT_DIR / "webui" / "Main.py"


def _module_literal(name: str):
    tree = ast.parse(WEBUI_MAIN.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )
    return ast.literal_eval(assignment.value)


def _function_source(name: str) -> str:
    source = WEBUI_MAIN.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, function) or ""


def test_webui_uses_thin_default_subtitle_stroke():
    assert _module_literal("DEFAULT_SUBTITLE_SETTINGS")["stroke_width"] == 1.0


def test_webui_defaults_emphasis_effects_to_enabled():
    restoration = _function_source("_apply_pending_task_restore")
    rendering = _function_source("_render_emphasis_settings")

    assert 'params.get("emphasis_enabled", True)' in restoration
    assert 'setdefault("emphasis_enabled_checkbox", True)' in rendering


def test_webui_defaults_to_automatic_local_voice_and_ordered_five_second_clips():
    initialization = _function_source("_initialize_session_state")
    restoration = _function_source("_apply_pending_task_restore")
    video_settings = _function_source("_render_video_settings")
    audio_settings = _function_source("_render_audio_settings")

    assert 'config.app.get("match_materials_to_script", True)' in initialization
    assert 'params.get("video_clip_duration", 5)' in restoration
    assert 'params.get("match_materials_to_script", True)' in restoration
    assert 'default_value=5' in video_settings
    assert 'config.ui.get("voice_name", "local:default") or "local:default"' in audio_settings


def test_webui_locks_emphasis_to_packaged_fangzheng_cartoon_and_fixed_pop_animation():
    restoration = _function_source("_apply_pending_task_restore")
    rendering = _function_source("_render_emphasis_settings")

    assert 'emphasis_font_name_select' not in restoration
    assert 'params.get("emphasis_random_animations", False)' in restoration
    assert 'params.emphasis_font_name = "FZKaTongJianTi.ttf"' in rendering
    assert '"emphasis_random_animations_checkbox", False' in rendering


def test_webui_takes_emphasis_terms_from_markdown_rows_only():
    rendering = _function_source("_render_emphasis_settings")

    assert 'params.emphasis_terms = ""' in rendering
    assert 'st.text_area(' not in rendering
    assert 'tr("Manual Emphasis Terms")' not in rendering


def test_apply_document_sets_content_without_generation_settings():
    document = script_document.parse_markdown_script(
        """# 婚姻二字

| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
| 1 | 第一行文案。 | 第一行 | wedding couple；married couple |
| 2 | 第二行文案。 | | |
"""
    )
    params = VideoParams(
        video_subject="旧标题",
        video_terms=["legacy term"],
        voice_name="local:default",
        video_aspect=VideoAspect.landscape,
    )

    script_document.apply_to_video_params(document, params)

    assert params.video_subject == "婚姻二字"
    assert params.video_script == "第一行文案。\n第二行文案。"
    assert params.video_terms == ["wedding couple", "married couple"]
    assert params.markdown_script == document
    assert params.voice_name == "local:default"
    assert params.video_aspect == VideoAspect.landscape


def test_webui_initializes_and_restores_serialized_markdown_document():
    initialization = _function_source("_initialize_session_state")
    restoration = _function_source("_apply_pending_task_restore")

    for key in (
        "markdown_script_document",
        "markdown_script_error",
        "markdown_script_hash",
    ):
        assert key in initialization
    assert 'params.get("markdown_script")' in restoration
    assert 'st.session_state["markdown_script_document"]' in restoration
    assert 'st.session_state.pop("markdown_script_uploader", None)' in restoration
    assert 'st.session_state["markdown_script_uploader"] =' not in restoration


def test_webui_renders_markdown_upload_preview_and_generation_guards():
    script_settings = _function_source("_render_script_settings")
    generation_controls = _function_source("_render_generation_controls")

    assert "file_uploader" in script_settings
    assert "parse_markdown_upload" in script_settings
    assert "apply_to_video_params" in script_settings
    assert "model_dump" in script_settings
    assert "st.data_editor" in script_settings
    assert "SelectboxColumn" in script_settings
    assert 'key="markdown_script_editor"' in script_settings
    assert 'key="markdown_emphasis_position_editor"' in script_settings
    assert "disabled=markdown_import_active" not in script_settings
    assert "uploaded_markdown is None" in script_settings
    assert "markdown_script_hash" in script_settings
    assert 'tr("Clear Markdown Import")' in script_settings
    assert "Markdown Parse Error" in script_settings
    assert 'tr("Markdown Requires Online Material Source")' in generation_controls
    assert "markdown_script_error" in generation_controls
    assert "markdown_source_invalid" in generation_controls
    assert "disabled=" in generation_controls


def test_markdown_preview_dataframe_columns_use_localized_labels():
    script_settings = _function_source("_render_script_settings")

    for label in (
        "Row",
        "Video Script",
        "Manual Emphasis Terms",
        "Markdown Search Terms",
        "Emphasis Position",
        "Emphasis Term",
        "Emphasis Position Help",
        "Scene",
        "Rows",
        "Markdown Scene Search Terms",
    ):
        assert f'tr("{label}")' in script_settings


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
    ):
        assert forbidden not in source
        assert forbidden not in settings
        assert forbidden not in script_settings

    for forbidden in (
        "llm.generate_script",
        "llm.generate_terms",
        "llm.test_connection",
        "LLM_PROVIDER_REGISTRY",
        "get_llm_provider",
    ):
        assert forbidden not in source

    assert 'tr("Import Markdown Script")' in script_settings
    assert 'tr("Markdown Parse Preview")' in script_settings
    assert 'tr("Markdown Script Help")' in script_settings
    assert "apply_to_video_params" in script_settings


def test_generation_controls_require_markdown_document():
    generation_controls = _function_source("_render_generation_controls")
    tree = ast.parse(generation_controls)

    generation_disabled_assign = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "generation_disabled"
            for target in node.targets
        )
    )
    generation_disabled_value = ast.unparse(generation_disabled_assign.value)
    assert "not params.markdown_script" in generation_disabled_value
    assert "markdown_script_error" in generation_disabled_value

    generate_video_button_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "button"
        and any(
            keyword.arg == "key"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "generate_video_button"
            for keyword in node.keywords
        )
    ]
    assert generate_video_button_calls

    disabled_keyword = next(
        (
            keyword
            for keyword in generate_video_button_calls[0].keywords
            if keyword.arg == "disabled"
        ),
        None,
    )
    assert disabled_keyword is not None
    assert "generation_disabled" in ast.unparse(disabled_keyword.value)
    assert 'tr("Generate Video")' in generation_controls
