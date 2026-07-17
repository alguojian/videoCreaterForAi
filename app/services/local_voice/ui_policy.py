from __future__ import annotations

LOCAL_TTS_SERVER = "local-cosyvoice"
LOCAL_TTS_SERVER_OPTIONS = ((LOCAL_TTS_SERVER, "Local CosyVoice3"),)


def normalize_tts_server(value: str | None) -> str:
    """Return the only provider available for automatic local voiceover."""
    return LOCAL_TTS_SERVER


def automatic_tts_server_options() -> list[tuple[str, str]]:
    """Return a fresh list for the automatic voiceover UI."""
    return list(LOCAL_TTS_SERVER_OPTIONS)
