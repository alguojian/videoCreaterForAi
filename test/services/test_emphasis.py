from pathlib import Path

import pytest

from app.services import emphasis
from app.services.emphasis import (
    EMPHASIS_ANIMATIONS,
    EMPHASIS_COLORS,
    EMPHASIS_SOUND_IDS,
    EmphasisCue,
    build_emphasis_cues,
    load_emphasis_cues,
    load_sound_manifest,
    parse_manual_terms,
    read_wav_duration,
    sound_group_for_animation,
    write_emphasis_cues,
)
from app.services.script_document import TimedScriptRow


def test_manual_terms_override_automatic_terms_and_map_to_srt_timing():
    subtitles = [
        (1, "00:00:00,000 --> 00:00:03,000", "不是年轻人逃避吃苦而编出来的段子"),
    ]

    cues = build_emphasis_cues(
        task_id="demo-task",
        subtitles=subtitles,
        automatic_terms=["年轻人"],
        manual_terms="不是年轻人, 逃避吃苦而编",
    )

    assert [cue.text for cue in cues] == ["不是年轻人", "逃避吃苦而编"]
    assert cues[0].start == pytest.approx(0.0)
    assert cues[0].end > cues[0].start
    assert cues[1].start > cues[0].start
    assert all(cue.end <= 3.0 for cue in cues)


def test_build_emphasis_cues_uses_seeded_styles_and_skips_unmatched_terms():
    subtitles = [(1, "00:00:00,000 --> 00:00:02,000", "婚姻需要共同经营")]

    first = build_emphasis_cues("same-task", subtitles, ["共同经营", "不存在"])
    second = build_emphasis_cues("same-task", subtitles, ["共同经营", "不存在"])

    assert first == second
    assert [cue.text for cue in first] == ["共同经营"]
    assert first[0].color in EMPHASIS_COLORS
    assert first[0].animation in EMPHASIS_ANIMATIONS
    assert first[0].sound_id in EMPHASIS_SOUND_IDS
    assert sound_group_for_animation(first[0].animation, first[0].sound_id) in {
        "pop",
        "whoosh",
        "hit",
        "sparkle",
    }


def test_build_emphasis_cues_falls_back_to_cue_prefixes_when_terms_are_empty():
    subtitles = [
        (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要共同经营"),
        (2, "00:00:02,000 --> 00:00:04,000", "愿意一起面对生活"),
    ]

    cues = build_emphasis_cues("fallback-task", subtitles, [])

    assert [cue.text for cue in cues] == ["婚姻需要共同", "愿意一起面对"]


def test_same_subtitle_terms_overlap_in_three_distinct_layers():
    subtitles = [
        (7, "00:00:00,000 --> 00:00:04,000", "婚姻不是逃避而是共同成长"),
    ]

    cues = build_emphasis_cues(
        "layered-task",
        subtitles,
        ["婚姻", "逃避", "共同成长"],
    )

    assert [cue.subtitle_index for cue in cues] == [7, 7, 7]
    assert [cue.layer for cue in cues] == [0, 1, 2]
    assert {cue.position for cue in cues}.issubset(set(emphasis.EMPHASIS_POSITIONS))
    assert cues[0].start < cues[1].start < cues[2].start
    assert [cue.end for cue in cues] == [4.0, 4.0, 4.0]


def test_emphasis_cues_disappear_when_their_sentence_ends():
    subtitles = [
        (1, "00:00:00,000 --> 00:00:06,000", "你好啊。今天天气怎么样？"),
    ]

    cues = build_emphasis_cues(
        "sentence-end-task",
        subtitles,
        ["你好啊", "今天天气怎么样"],
    )

    assert [cue.text for cue in cues] == ["你好啊", "今天天气怎么样"]
    assert cues[0].end == pytest.approx(1.8)
    assert cues[1].end == pytest.approx(6.0)


def test_markdown_emphasis_cues_disappear_when_their_sentence_ends():
    rows = [
        TimedScriptRow(1, "你好啊。今天天气怎么样？", ("你好啊", "今天天气怎么样"), 0.0, 6.0),
    ]

    cues = emphasis.build_markdown_emphasis_cues("sentence-end-task", rows)

    assert cues[0].end == pytest.approx(1.8)
    assert cues[1].end == pytest.approx(6.0)


def test_fourth_term_replaces_the_old_high_layer_term():
    subtitles = [
        (2, "00:00:00,000 --> 00:00:08,000", "甲乙丙丁戊己庚辛"),
    ]

    cues = build_emphasis_cues(
        "rolling-task",
        subtitles,
        ["甲乙", "丙丁", "戊己", "庚辛"],
    )

    assert [cue.layer for cue in cues] == [0, 1, 2, 0]
    assert cues[0].end == pytest.approx(cues[3].start)
    assert cues[1].end == pytest.approx(8.0)
    assert cues[2].end == pytest.approx(8.0)
    assert cues[3].end == pytest.approx(8.0)


def test_old_emphasis_payload_defaults_to_the_high_layer():
    cue = EmphasisCue.from_dict(
        {
            "text": "婚姻二字",
            "start": 0.0,
            "end": 1.0,
            "color": "#FF5A36",
            "animation": "pop",
            "sound_id": "pop-01",
            "position": "left",
        }
    )

    assert cue.subtitle_index == -1
    assert cue.source_row_number == -1
    assert cue.layer == 0


def test_emphasis_payload_round_trips_source_row_number():
    cue = EmphasisCue(
        text="婚姻二字",
        start=0.0,
        end=1.0,
        color="#FF5A36",
        animation="pop",
        sound_id="pop-01",
        position="left",
        source_row_number=2,
    )

    assert EmphasisCue.from_dict(cue.to_dict()) == cue


@pytest.mark.parametrize("source_row_number", [0, -2])
def test_emphasis_cue_rejects_invalid_source_row_number(source_row_number):
    with pytest.raises(ValueError, match="source row number"):
        EmphasisCue(
            text="婚姻二字",
            start=0.0,
            end=1.0,
            color="#FF5A36",
            animation="pop",
            sound_id="pop-01",
            position="left",
            source_row_number=source_row_number,
        )


@pytest.mark.parametrize("source_row_number", [True, False, 1.0, 1.9, "1", None])
def test_emphasis_cue_rejects_non_integer_source_row_number(source_row_number):
    with pytest.raises(ValueError, match="source row number must be an integer"):
        EmphasisCue(
            text="婚姻二字",
            start=0.0,
            end=1.0,
            color="#FF5A36",
            animation="pop",
            sound_id="pop-01",
            position="left",
            source_row_number=source_row_number,
        )


@pytest.mark.parametrize("source_row_number", [True, False, 1.0, 1.9, "1", None])
def test_emphasis_payload_rejects_non_integer_source_row_number(source_row_number):
    payload = {
        "text": "婚姻二字",
        "start": 0.0,
        "end": 1.0,
        "color": "#FF5A36",
        "animation": "pop",
        "sound_id": "pop-01",
        "position": "left",
        "source_row_number": source_row_number,
    }

    with pytest.raises(ValueError, match="source row number must be an integer"):
        EmphasisCue.from_dict(payload)


def test_markdown_and_legacy_cues_with_the_same_numeric_index_use_separate_groups():
    cues = [
        EmphasisCue(
            "legacyfirst",
            0.0,
            5.0,
            "#FF5A36",
            "pop",
            "pop-01",
            "left",
            subtitle_index=1,
        ),
        EmphasisCue(
            "markdownfirst",
            1.0,
            5.0,
            "#FFB000",
            "pop",
            "pop-02",
            "center",
            source_row_number=1,
        ),
        EmphasisCue(
            "legacysecond",
            2.0,
            5.0,
            "#A86BFF",
            "pop",
            "pop-03",
            "right",
            subtitle_index=1,
        ),
        EmphasisCue(
            "markdownsecond",
            3.0,
            5.0,
            "#22C7A8",
            "shake",
            "pop-01",
            "left",
            source_row_number=1,
        ),
    ]

    arranged = emphasis._arrange_grouped_cues("mixed-groups", cues)
    by_text = {cue.text: cue for cue in arranged}

    assert by_text["legacyfirst"].layer == 0
    assert by_text["legacysecond"].layer == 1
    assert by_text["markdownfirst"].layer == 0
    assert by_text["markdownsecond"].layer == 1
    assert all(cue.end == 5.0 for cue in arranged)


def test_grouping_keeps_each_cue_weighted_position_while_assigning_safe_layers():
    cues = [
        EmphasisCue(
            "aa",
            0.0,
            4.0,
            "#FF5A36",
            "pop",
            "pop-01",
            "left",
            subtitle_index=7,
        ),
        EmphasisCue(
            "bb",
            1.0,
            4.0,
            "#FFB000",
            "pop",
            "pop-02",
            "left",
            subtitle_index=7,
        ),
        EmphasisCue(
            "cc",
            2.0,
            4.0,
            "#A86BFF",
            "pop",
            "pop-03",
            "left",
            subtitle_index=7,
        ),
    ]

    arranged = emphasis._arrange_grouped_cues("legacy-seed", cues)

    assert [(cue.layer, cue.position, cue.end) for cue in arranged] == [
        (0, "left", 4.0),
        (1, "left", 4.0),
        (2, "left", 4.0),
    ]


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
    assert [cue.layer for cue in cues] == [0, 1]
    assert cues[0].end == cues[1].end == 3.0


def test_markdown_emphasis_preserves_duplicate_terms_listed_in_one_row():
    rows = [TimedScriptRow(1, "婚姻需要婚姻", ("婚姻", "婚姻"), 0.0, 2.0)]

    cues = emphasis.build_markdown_emphasis_cues("task-md", rows)

    assert [cue.text for cue in cues] == ["婚姻", "婚姻"]
    assert [cue.layer for cue in cues] == [0, 1]


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (TimedScriptRow(1, "，。 ", ("婚姻",), 0.0, 2.0), "第 1 行.*可见文本为空"),
        (TimedScriptRow(2, "婚姻需要沟通", ("婚姻",), 2.0, 2.0), "第 2 行.*时间范围"),
        (TimedScriptRow(3, "婚姻需要沟通", ("，。",), 0.0, 2.0), "第 3 行.*重点词.*为空"),
        (TimedScriptRow(4, "婚姻需要沟通", ("耐心",), 0.0, 2.0), "第 4 行.*耐心"),
    ],
)
def test_markdown_emphasis_rejects_invalid_timed_rows(row, message):
    with pytest.raises(ValueError, match=message):
        emphasis.build_markdown_emphasis_cues("task-md", [row])


def test_write_and_load_emphasis_cues_round_trip(tmp_path: Path):
    cues = build_emphasis_cues(
        "round-trip-task",
        [(1, "00:00:00,000 --> 00:00:02,000", "婚姻需要共同经营")],
        ["共同经营"],
    )
    output = tmp_path / "emphasis.json"

    write_emphasis_cues(cues, output)

    assert load_emphasis_cues(output) == cues


def test_parse_manual_terms_deduplicates_chinese_and_english_commas():
    assert parse_manual_terms("婚姻二字，逃避吃苦, 婚姻二字\n一起面对") == [
        "婚姻二字",
        "逃避吃苦",
        "一起面对",
    ]


def test_emphasis_sound_manifest_has_twelve_short_local_wav_files():
    project_root = Path(__file__).parent.parent.parent

    manifest = load_sound_manifest(project_root)

    assert len(manifest) == 12
    assert {item.group for item in manifest} == {"pop", "whoosh", "hit", "sparkle"}
    assert all((project_root / item.file).is_file() for item in manifest)
    assert all(read_wav_duration(project_root / item.file) <= 0.6 for item in manifest)
