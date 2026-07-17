from __future__ import annotations

from collections.abc import Iterable


DEFAULT_VIDEO_CODEC = "libx264"
AUTO_VIDEO_CODEC = "auto"
PREFERRED_HARDWARE_CODEC = "h264_nvenc"


def choose_video_codec(
    preferred: str | None,
    available_codecs: Iterable[str],
    disabled_codecs: Iterable[str] = (),
) -> str:
    selected = str(preferred or AUTO_VIDEO_CODEC).strip().lower()
    available = set(available_codecs)
    disabled = set(disabled_codecs)
    if selected not in {"", AUTO_VIDEO_CODEC}:
        return selected
    if PREFERRED_HARDWARE_CODEC in available and PREFERRED_HARDWARE_CODEC not in disabled:
        return PREFERRED_HARDWARE_CODEC
    return DEFAULT_VIDEO_CODEC


def is_fast_subtitle_path_supported(params, subtitle_path: str | None) -> bool:
    if not subtitle_path or not getattr(params, "subtitle_enabled", False):
        return False
    if getattr(params, "emphasis_enabled", False):
        return False
    if getattr(params, "text_background_color", False):
        return False
    if getattr(params, "rounded_subtitle_background", False):
        return False
    return getattr(params, "subtitle_position", "bottom") in {
        "top",
        "center",
        "bottom",
    }
