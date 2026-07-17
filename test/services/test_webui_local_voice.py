from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.config import config


ROOT_DIR = Path(__file__).parent.parent.parent
WEBUI_MAIN = ROOT_DIR / "webui" / "Main.py"


def _run_webui():
    app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=30)
    app.run()
    return app


def _voice_mode_control(app):
    return next(
        item
        for item in app.segmented_control
        if str(item.key).startswith("voice_mode_control")
    )


def test_automatic_voiceover_exposes_only_local_cosyvoice():
    app = _run_webui()
    _voice_mode_control(app).set_value("Auto").run()

    assert not any(
        str(item.key).startswith("tts_server_select") for item in app.selectbox
    )
    assert any("Local CosyVoice3" in str(item.value) for item in app.info)
    local_voice = next(
        item
        for item in app.selectbox
        if str(item.key).startswith("speech_synthesis_select_local-cosyvoice")
    )
    assert "Local voice: default" in [str(option) for option in local_voice.options]


def test_upload_and_none_voiceover_modes_remain_available():
    app = _run_webui()
    control = _voice_mode_control(app)

    assert {str(option) for option in control.options} == {"Auto", "Upload", "None"}

    control.set_value("Auto").run()
    control.set_value("Upload").run()
    assert any(
        str(item.key) == "custom_audio_file_uploader" for item in app.file_uploader
    )
    assert not any(
        str(item.key).startswith("speech_synthesis_select_")
        for item in app.selectbox
    )


def test_legacy_no_voice_server_stays_in_no_voice_mode():
    original_ui = dict(config.ui)
    try:
        config.ui.pop("voice_mode", None)
        config.ui["tts_server"] = "no-voice"

        app = _run_webui()

        assert _voice_mode_control(app).value == "none"
    finally:
        config.ui.clear()
        config.ui.update(original_ui)

    control = _voice_mode_control(app)
    control.set_value("None").run()
    assert not any(
        str(item.key) == "custom_audio_file_uploader" for item in app.file_uploader
    )
    assert not any(
        str(item.key).startswith("speech_synthesis_select_")
        for item in app.selectbox
    )
