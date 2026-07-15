from pathlib import Path

import pytest

from app.services.local_voice.models import AlignmentCharacter
from app.services.local_voice.subtitle_builder import build_cues, write_srt


def _alignment(text: str, step: float = 0.2) -> list[AlignmentCharacter]:
    return [
        AlignmentCharacter(char=char, index=index, start=index * step, end=(index + 1) * step)
        for index, char in enumerate(text)
    ]


def test_build_cues_uses_display_text_when_spoken_text_differs():
    cues = build_cues(
        _alignment("一二三四"),
        display_text="1234",
        max_chars=20,
        min_duration=0.1,
        max_duration=4.2,
        audio_duration=2.0,
    )

    assert len(cues) == 1
    assert cues[0].text == "1234"
    assert cues[0].start == pytest.approx(0.0)
    assert cues[0].end == pytest.approx(0.8)


def test_build_cues_splits_at_punctuation_and_max_chars():
    cues = build_cues(
        _alignment("你好世界。欢迎关注我们。", step=0.1),
        display_text="你好世界。欢迎关注我们。",
        max_chars=8,
        min_duration=0.1,
        max_duration=4.2,
        audio_duration=3.0,
    )

    assert [cue.text for cue in cues] == ["你好世界", "欢迎关注我们"]
    assert all("。" not in cue.text for cue in cues)
    assert all(cue.start < cue.end <= 3.0 for cue in cues)
    assert all(cues[i].end <= cues[i + 1].start for i in range(len(cues) - 1))


def test_build_cues_hides_punctuation_and_merges_short_semantic_segments():
    text = "婚姻二字，写起来只有两笔，走进去却是一生的功课。"

    cues = build_cues(
        _alignment(text, step=0.1),
        display_text=text,
        max_chars=14,
        min_duration=0.1,
        max_duration=4.2,
        audio_duration=10.0,
    )

    assert [cue.text for cue in cues] == [
        "婚姻二字写起来只有两笔",
        "走进去却是一生的功课",
    ]
    assert all("，" not in cue.text and "。" not in cue.text for cue in cues)
    assert all("\n" not in cue.text for cue in cues)
    assert all(len(cue.text) <= 14 for cue in cues)


def test_build_cues_does_not_leave_a_short_tail_after_punctuation():
    text = "这是一个完整的句子，尾巴。"

    cues = build_cues(
        _alignment(text, step=0.1),
        display_text=text,
        max_chars=14,
        min_duration=0.1,
        max_duration=4.2,
        audio_duration=10.0,
    )

    assert [cue.text for cue in cues] == ["这是一个完整的句子尾巴"]
    assert len(cues[0].text) > 2


def test_write_srt_formats_milliseconds(tmp_path: Path):
    cues = build_cues(
        _alignment("测试", step=0.5),
        display_text="测试",
        min_duration=0.1,
        max_duration=4.2,
        audio_duration=2.0,
    )
    path = tmp_path / "subtitle.srt"

    write_srt(cues, path)

    assert path.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:01,000\n测试\n\n"
    )


def test_build_cues_rejects_empty_alignment():
    with pytest.raises(ValueError, match="alignment must not be empty"):
        build_cues([], display_text="测试")
