from types import SimpleNamespace

from app.services.video_acceleration import (
    choose_video_codec,
    is_fast_subtitle_path_supported,
)


def _params(**overrides):
    values = {
        "subtitle_enabled": True,
        "subtitle_position": "bottom",
        "text_background_color": False,
        "rounded_subtitle_background": False,
        "emphasis_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_auto_codec_prefers_nvenc_when_available():
    assert choose_video_codec("auto", {"libx264", "h264_nvenc"}) == "h264_nvenc"


def test_auto_codec_falls_back_to_cpu_when_nvenc_is_unavailable():
    assert choose_video_codec("auto", {"libx264"}) == "libx264"


def test_explicit_codec_is_preserved():
    assert choose_video_codec("libx264", {"libx264", "h264_nvenc"}) == "libx264"


def test_common_subtitles_use_fast_ffmpeg_path():
    assert is_fast_subtitle_path_supported(_params(), "subtitle.srt") is True


def test_advanced_subtitles_keep_moviepy_path():
    assert is_fast_subtitle_path_supported(
        _params(text_background_color="#000000") , "subtitle.srt"
    ) is False
    assert is_fast_subtitle_path_supported(
        _params(subtitle_position="custom"), "subtitle.srt"
    ) is False
    assert is_fast_subtitle_path_supported(
        _params(emphasis_enabled=True), "subtitle.srt"
    ) is False
