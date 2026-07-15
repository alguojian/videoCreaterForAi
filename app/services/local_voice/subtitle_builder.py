from __future__ import annotations

from pathlib import Path

from .exceptions import SubtitleMappingError
from .models import AlignmentCharacter, SubtitleCue


_BOUNDARY_PUNCTUATION = set("。！？!?；;，,.\n")
_HIDDEN_DISPLAY_PUNCTUATION = set("，,。.")


def _is_hidden_display_char(char: str) -> bool:
    return char in _HIDDEN_DISPLAY_PUNCTUATION


def _is_visible_display_char(char: str) -> bool:
    return not char.isspace() and not _is_hidden_display_char(char)


def _timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _display_anchors(
    alignment: list[AlignmentCharacter], display_text: str
) -> list[tuple[str, float, float]]:
    visible_count = sum(_is_visible_display_char(char) for char in display_text)
    if visible_count == 0:
        raise ValueError("display_text must not be empty")

    # The aligner may include punctuation while display text can be normalized
    # (for example, spoken Chinese characters mapped to Arabic numerals).  When
    # the visible character counts line up, use the aligner's visible spans
    # directly so punctuation never shifts the subtitle timing.
    aligned_visible = [
        item for item in alignment if _is_visible_display_char(item.char)
    ]
    direct_mapping = len(aligned_visible) == visible_count

    anchors: list[tuple[str, float, float]] = []
    visible_index = 0
    last_anchor = alignment[0]
    denominator = max(visible_count - 1, 1)
    fallback_alignment = aligned_visible or alignment
    for char in display_text:
        if not _is_visible_display_char(char):
            anchors.append((char, last_anchor.start, last_anchor.end))
            continue
        if direct_mapping:
            last_anchor = aligned_visible[visible_index]
        else:
            source_index = round(
                visible_index * (len(fallback_alignment) - 1) / denominator
            )
            last_anchor = fallback_alignment[source_index]
        anchors.append((char, last_anchor.start, last_anchor.end))
        visible_index += 1
    return anchors


def build_cues(
    alignment: list[AlignmentCharacter],
    display_text: str,
    *,
    min_duration: float = 0.85,
    max_duration: float = 4.2,
    max_chars: int = 20,
    gap_seconds: float = 0.04,
    audio_duration: float | None = None,
) -> list[SubtitleCue]:
    if not alignment:
        raise ValueError("alignment must not be empty")
    if min_duration <= 0 or max_duration <= 0 or min_duration > max_duration:
        raise ValueError("subtitle duration limits are invalid")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if gap_seconds < 0:
        raise ValueError("gap_seconds must be non-negative")
    if audio_duration is not None and audio_duration <= 0:
        raise ValueError("audio_duration must be positive")

    anchors = _display_anchors(alignment, display_text)
    segments: list[tuple[str, float, float]] = []
    current: list[tuple[str, float, float]] = []
    visible_count = 0

    def flush() -> None:
        nonlocal current, visible_count
        text = "".join(
            item[0]
            for item in current
            if _is_visible_display_char(item[0])
        ).strip()
        if text:
            segments.append(
                (
                    text,
                    min(item[1] for item in current),
                    max(item[2] for item in current),
                )
            )
        current = []
        visible_count = 0

    for item in anchors:
        char = item[0]
        current.append(item)
        if _is_visible_display_char(char):
            visible_count += 1
        if char in _BOUNDARY_PUNCTUATION or visible_count >= max_chars:
            flush()
    flush()

    # Punctuation remains a semantic boundary, but a short phrase should be
    # packed with the next phrase when it still fits.  This avoids subtitles
    # such as a standalone two- or three-character tail after a full sentence.
    raw_cues: list[tuple[str, float, float]] = []
    pending: tuple[str, float, float] | None = None
    for text, start, end in segments:
        if pending is None:
            pending = (text, start, end)
            continue
        pending_text, pending_start, pending_end = pending
        if len(pending_text) + len(text) <= max_chars:
            pending = (
                pending_text + text,
                min(pending_start, start),
                max(pending_end, end),
            )
        else:
            raw_cues.append(pending)
            pending = (text, start, end)
    if pending is not None:
        raw_cues.append(pending)

    cues: list[SubtitleCue] = []
    previous_end = 0.0
    for index, (text, raw_start, raw_end) in enumerate(raw_cues, start=1):
        start = max(raw_start, previous_end + (gap_seconds if cues else 0.0))
        end = max(raw_end, start + min_duration)
        end = min(end, start + max_duration)
        if audio_duration is not None:
            end = min(end, audio_duration)
        if end <= start:
            raise SubtitleMappingError(f"invalid subtitle timing for cue {index}: {text}")
        cue = SubtitleCue(index=index, start=start, end=end, text=text)
        cues.append(cue)
        previous_end = cue.end
    return cues


def write_srt(cues: list[SubtitleCue], output_path: str | Path) -> None:
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        f"{cue.index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}\n\n"
        for cue in cues
    )
    try:
        output.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise SubtitleMappingError(f"failed to write subtitle file: {output}") from exc
