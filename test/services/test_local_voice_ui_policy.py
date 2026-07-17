from app.services.local_voice.ui_policy import (
    LOCAL_TTS_SERVER,
    automatic_tts_server_options,
    normalize_tts_server,
)


def test_automatic_tts_has_only_local_cosyvoice3():
    assert automatic_tts_server_options() == [
        (LOCAL_TTS_SERVER, "Local CosyVoice3")
    ]


def test_saved_provider_values_always_normalize_to_local_cosyvoice():
    for saved_value in (None, "", "azure-tts-v1", "siliconflow", "chatterbox"):
        assert normalize_tts_server(saved_value) == LOCAL_TTS_SERVER
