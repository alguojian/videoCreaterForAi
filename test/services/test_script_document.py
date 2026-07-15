from dataclasses import FrozenInstanceError

import pytest

from app.services import script_document


VALID_MARKDOWN = r"""# 婚姻二字

| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
| 1 | 婚姻二字写起来只有两笔。 | 婚姻二字；只有两笔 | wedding couple；married couple |
| 2 | 走进去，却是一生的功课。 | 一生的功课 | |
| 3 | 幸福靠沟通\|理解共同守护。 | 沟通;理解 | couple talking;healthy relationship |
"""


def test_parse_markdown_builds_title_rows_terms_and_script_text():
    document = script_document.parse_markdown_script(VALID_MARKDOWN)

    assert document.title == "婚姻二字"
    assert [row.number for row in document.rows] == [1, 2, 3]
    assert document.rows[0].emphasis_terms == ["婚姻二字", "只有两笔"]
    assert document.rows[2].emphasis_terms == ["沟通", "理解"]
    assert document.rows[2].text == "幸福靠沟通|理解共同守护。"
    assert document.script_text() == (
        "婚姻二字写起来只有两笔。\n"
        "走进去，却是一生的功课。\n"
        "幸福靠沟通|理解共同守护。"
    )


def test_build_scenes_inherits_blank_search_terms_until_next_scene():
    document = script_document.parse_markdown_script(VALID_MARKDOWN)

    scenes = script_document.build_scenes(document)

    assert scenes == [
        script_document.ScriptScene(
            index=1,
            first_row=1,
            last_row=2,
            search_terms=("wedding couple", "married couple"),
        ),
        script_document.ScriptScene(
            index=2,
            first_row=3,
            last_row=3,
            search_terms=("couple talking", "healthy relationship"),
        ),
    ]
    with pytest.raises(FrozenInstanceError):
        scenes[0].last_row = 99


def test_build_scenes_rejects_a_direct_document_without_a_first_scene():
    document = script_document.MarkdownScriptDocument(
        title="首场景缺失",
        rows=[
            script_document.MarkdownScriptRow(
                number=1,
                text="第一行没有素材搜索词。",
                emphasis_terms=[],
                material_search_terms=[],
            ),
            script_document.MarkdownScriptRow(
                number=2,
                text="第二行才提供素材搜索词。",
                emphasis_terms=[],
                material_search_terms=["wedding"],
            ),
        ],
    )

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="第一行必须提供素材搜索词",
    ):
        script_document.build_scenes(document)


def test_lists_accept_chinese_and_english_semicolons():
    document = script_document.parse_markdown_script(
        VALID_MARKDOWN.replace(
            "婚姻二字；只有两笔",
            "婚姻二字;只有两笔",
        ).replace(
            "couple talking;healthy relationship",
            "couple talking；healthy relationship",
        )
    )

    assert document.rows[0].emphasis_terms == ["婚姻二字", "只有两笔"]
    assert document.rows[2].material_search_terms == [
        "couple talking",
        "healthy relationship",
    ]


def test_duplicate_lists_are_deduplicated_in_order_with_exact_warnings():
    markdown = VALID_MARKDOWN.replace(
        "婚姻二字；只有两笔",
        "婚姻二字；只有两笔；婚姻二字",
    ).replace(
        "wedding couple；married couple",
        "wedding couple；married couple；wedding couple",
    )

    document = script_document.parse_markdown_script(markdown)

    assert document.rows[0].emphasis_terms == ["婚姻二字", "只有两笔"]
    assert document.rows[0].material_search_terms == [
        "wedding couple",
        "married couple",
    ]
    assert document.warnings == [
        "第 1 行重点词存在重复项，已按原顺序去重",
        "第 1 行素材搜索词存在重复项，已按原顺序去重",
    ]


def test_visible_text_removes_whitespace_and_unicode_punctuation():
    assert script_document.visible_text(" 婚 姻，二字！Promise's。 ") == "婚姻二字Promises"


def test_parse_markdown_upload_accepts_case_insensitive_md_extension():
    document = script_document.parse_markdown_upload(
        "marriage.MD",
        VALID_MARKDOWN.encode("utf-8"),
    )

    assert document.title == "婚姻二字"


def test_parse_markdown_upload_accepts_utf8_bom():
    document = script_document.parse_markdown_upload(
        "marriage.md",
        VALID_MARKDOWN.encode("utf-8-sig"),
    )

    assert document.title == "婚姻二字"


def test_parse_markdown_upload_rejects_non_md_extension():
    with pytest.raises(script_document.MarkdownScriptError, match=r"\.md"):
        script_document.parse_markdown_upload(
            "marriage.txt",
            VALID_MARKDOWN.encode("utf-8"),
        )


def test_parse_markdown_upload_rejects_non_utf8_payload():
    with pytest.raises(script_document.MarkdownScriptError, match="UTF-8"):
        script_document.parse_markdown_upload("marriage.md", b"\xff\xfe")


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        (
            VALID_MARKDOWN.replace("# 婚姻二字\n\n", "", 1),
            "一级标题",
        ),
        (
            VALID_MARKDOWN.replace(
                "# 婚姻二字\n\n",
                "# 婚姻二字\n\n额外说明\n\n",
                1,
            ),
            "标题和表格之外",
        ),
        (
            f"{VALID_MARKDOWN}\n额外说明\n",
            "标题和表格之外",
        ),
        (
            VALID_MARKDOWN.replace(
                "| 序号 | 口播文案 | 重点词 | 素材搜索词 |",
                "| 序号 | 重点词 | 口播文案 | 素材搜索词 |",
                1,
            ),
            "固定四列表格",
        ),
        (
            VALID_MARKDOWN.replace(
                "|---:|---|---|---|",
                "|---:|---|invalid|---|",
                1,
            ),
            "表格分隔行无效",
        ),
        (
            "\n".join(VALID_MARKDOWN.splitlines()[:4]) + "\n",
            "口播表格不能为空",
        ),
        (
            VALID_MARKDOWN.replace("| 1 |", "| 2 |", 1),
            "序号必须从 1 开始连续递增",
        ),
        (
            VALID_MARKDOWN.replace("| 2 |", "| 3 |", 1),
            "序号必须从 1 开始连续递增",
        ),
        (
            VALID_MARKDOWN.replace("| 2 |", "| 1 |", 1),
            "序号必须从 1 开始连续递增",
        ),
        (
            VALID_MARKDOWN.replace(
                "| 2 | 走进去，却是一生的功课。 | 一生的功课 | |",
                "| 2 | | | |",
                1,
            ),
            "第 2 行口播文案不能为空",
        ),
        (
            VALID_MARKDOWN.replace(
                "| 2 | 走进去，却是一生的功课。 | 一生的功课 | |",
                "| 2 | 走进去，却是一生的功课。 | 一生的功课 |",
                1,
            ),
            "表格必须恰好包含四列",
        ),
        (
            VALID_MARKDOWN.replace("婚姻二字；只有两笔", "不存在的重点词", 1),
            "第 1 行重点词",
        ),
        (
            VALID_MARKDOWN.replace(
                "wedding couple；married couple",
                "婚礼夫妻",
                1,
            ),
            "素材搜索词必须使用英文",
        ),
        (
            VALID_MARKDOWN.replace(
                "wedding couple；married couple",
                "https://example.com",
                1,
            ),
            "素材搜索词必须使用英文",
        ),
        (
            VALID_MARKDOWN.replace(
                "wedding couple；married couple",
                r"wedding\\couple",
                1,
            ),
            "素材搜索词必须使用英文",
        ),
        (
            VALID_MARKDOWN.replace("wedding couple；married couple", "", 1),
            "第一行必须填写素材搜索词",
        ),
        (
            VALID_MARKDOWN.replace(
                "wedding couple；married couple",
                "wedding couple;married couple;bride smiling;wedding rings",
                1,
            ),
            "不能超过 3 个",
        ),
        (
            VALID_MARKDOWN.replace(r"沟通\|理解", "沟通|理解", 1),
            "恰好包含四列",
        ),
        (
            VALID_MARKDOWN.replace(
                "| 2 | 走进去，却是一生的功课。 | 一生的功课 | |",
                "2 | 走进去，却是一生的功课。 | 一生的功课 |",
                1,
            ),
            "竖线包围",
        ),
    ],
    ids=[
        "missing-title",
        "extra-before-table",
        "extra-after-table",
        "wrong-columns",
        "invalid-separator",
        "no-data-rows",
        "wrong-number",
        "later-number-gap",
        "later-number-duplicate",
        "empty-script-text",
        "data-row-missing-column",
        "emphasis-not-in-row",
        "chinese-search-term",
        "url-search-term",
        "path-search-term",
        "first-row-search-empty",
        "too-many-search-terms",
        "unescaped-pipe",
        "unwrapped-row",
    ],
)
def test_parse_markdown_rejects_invalid_documents(markdown, message):
    with pytest.raises(script_document.MarkdownScriptError, match=message):
        script_document.parse_markdown_script(markdown)


def test_parse_markdown_rejects_multiple_level_one_titles():
    markdown = VALID_MARKDOWN.replace(
        "# 婚姻二字\n",
        "# 婚姻二字\n\n# 第二个标题\n",
        1,
    )

    with pytest.raises(script_document.MarkdownScriptError, match="一级标题"):
        script_document.parse_markdown_script(markdown)


def test_parse_markdown_rejects_multiple_task_tables():
    markdown = f"{VALID_MARKDOWN.rstrip()}\n\n{VALID_MARKDOWN.split(chr(10), 2)[2]}"

    with pytest.raises(script_document.MarkdownScriptError, match="表格"):
        script_document.parse_markdown_script(markdown)


def test_parse_markdown_rejects_table_like_content_after_table_ending_blank():
    markdown = f"{VALID_MARKDOWN}\n| 4 | 空行后的额外行。 | | another scene |\n"

    with pytest.raises(
        script_document.MarkdownScriptError,
        match="标题和表格之外",
    ):
        script_document.parse_markdown_script(markdown)


@pytest.mark.parametrize(
    "search_term",
    ["123", "wedding &&"],
    ids=["numeric-only", "dangling-repeated-symbols"],
)
def test_parse_markdown_rejects_invalid_english_search_phrases(search_term):
    markdown = VALID_MARKDOWN.replace(
        "wedding couple；married couple",
        search_term,
        1,
    )

    with pytest.raises(
        script_document.MarkdownScriptError,
        match="素材搜索词必须使用英文",
    ):
        script_document.parse_markdown_script(markdown)


def test_parse_markdown_accepts_all_safe_search_term_characters():
    markdown = VALID_MARKDOWN.replace(
        "wedding couple；married couple",
        "bride's wedding;love & marriage;well-being 2",
        1,
    )

    document = script_document.parse_markdown_script(markdown)

    assert document.rows[0].material_search_terms == [
        "bride's wedding",
        "love & marriage",
        "well-being 2",
    ]


def _alignment_document():
    return script_document.parse_markdown_script(
        """# 重复词测试

| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
| 1 | 婚姻需要沟通。 | 婚姻 | wedding couple |
| 2 | 婚姻也需要耐心。 | 婚姻 | couple talking |
"""
    )


def test_align_rows_uses_sequential_occurrences_and_builds_scene_times():
    document = _alignment_document()
    subtitles = [
        (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通"),
        (2, "00:00:02.000 --> 00:00:04.500", "婚姻也需要耐心"),
    ]

    timed_rows = script_document.align_rows_to_subtitles(document, subtitles)
    timed_scenes = script_document.build_timed_scenes(document, timed_rows)

    assert [(row.number, row.start, row.end) for row in timed_rows] == [
        (1, 0.0, 2.0),
        (2, 2.0, 4.5),
    ]
    assert [(scene.start, scene.end) for scene in timed_scenes] == [
        (0.0, 2.0),
        (2.0, 4.5),
    ]


def test_align_rows_interpolates_two_sequential_rows_inside_one_subtitle():
    document = _alignment_document()

    timed_rows = script_document.align_rows_to_subtitles(
        document,
        [(1, "00:00:00,000 --> 00:00:13,000", "婚姻需要沟通婚姻也需要耐心")],
    )

    assert [(row.start, row.end) for row in timed_rows] == [
        (0.0, 6.0),
        (6.0, 13.0),
    ]


@pytest.mark.parametrize(
    "subtitles",
    [
        [
            (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通"),
            (2, "00:00:01,500 --> 00:00:04,500", "婚姻也需要耐心"),
        ],
        [
            (1, "00:00:03,000 --> 00:00:05,000", "婚姻需要沟通"),
            (2, "00:00:00,000 --> 00:00:03,000", "婚姻也需要耐心"),
        ],
    ],
    ids=["overlap", "reversed"],
)
def test_align_rows_rejects_overlapping_or_reversed_subtitle_timeline(subtitles):
    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="字幕 2.*重叠或倒序",
    ):
        script_document.align_rows_to_subtitles(_alignment_document(), subtitles)


def test_align_rows_validates_hidden_subtitle_time_range_before_skipping_text():
    subtitles = [
        (99, "不是 SRT 时间范围", "，。！？"),
        (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通"),
        (2, "00:00:02,000 --> 00:00:04,500", "婚姻也需要耐心"),
    ]

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="字幕 99.*时间范围",
    ):
        script_document.align_rows_to_subtitles(_alignment_document(), subtitles)


def test_align_rows_validates_hidden_subtitle_sequence_before_skipping_text():
    subtitles = [
        (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通"),
        (99, "00:00:01,000 --> 00:00:01,500", "，。！？"),
        (2, "00:00:02,000 --> 00:00:04,500", "婚姻也需要耐心"),
    ]

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="字幕 99.*重叠或倒序",
    ):
        script_document.align_rows_to_subtitles(_alignment_document(), subtitles)


def test_build_timed_scenes_uses_each_scenes_first_and_last_row_times():
    document = script_document.parse_markdown_script(VALID_MARKDOWN)
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 0.0, 2.0),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 2.0, 5.0),
        script_document.TimedScriptRow(3, document.rows[2].text, (), 5.0, 7.5),
    ]

    timed_scenes = script_document.build_timed_scenes(document, timed_rows)

    assert [
        (scene.first_row, scene.last_row, scene.start, scene.end)
        for scene in timed_scenes
    ] == [
        (1, 2, 0.0, 5.0),
        (3, 3, 5.0, 7.5),
    ]


def test_build_timed_scenes_partitions_the_full_target_timeline_without_gaps():
    document = script_document.parse_markdown_script(
        """# 婚姻时间线

| 序号 | 口播文案 | 重点词 | 素材搜索词 |
|---:|---|---|---|
| 1 | 第一行。 | | wedding couple |
| 2 | 第二行。 | | |
| 3 | 第三行。 | | couple talking |
| 4 | 第四行。 | | |
| 5 | 第五行。 | | happy marriage |
| 6 | 第六行。 | | |
"""
    )
    boundaries = [
        (0.08, 4.00),
        (4.20, 8.56),
        (8.88, 12.00),
        (12.20, 16.50),
        (16.54, 19.00),
        (19.10, 21.68),
    ]
    timed_rows = [
        script_document.TimedScriptRow(
            number=row.number,
            text=row.text,
            emphasis_terms=(),
            start=start,
            end=end,
        )
        for row, (start, end) in zip(document.rows, boundaries)
    ]

    timed_scenes = script_document.build_timed_scenes(
        document,
        timed_rows,
        total_duration=21.95,
    )

    assert [(scene.start, scene.end) for scene in timed_scenes] == [
        (0.0, 8.88),
        (8.88, 16.54),
        (16.54, 21.95),
    ]
    assert sum(scene.end - scene.start for scene in timed_scenes) == pytest.approx(
        21.95
    )
    assert all(
        current.end == following.start
        for current, following in zip(timed_scenes, timed_scenes[1:])
    )


@pytest.mark.parametrize(
    "total_duration",
    [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, 3.99],
    ids=["nan", "positive-infinity", "negative-infinity", "zero", "negative", "short"],
)
def test_build_timed_scenes_rejects_invalid_explicit_total_duration(total_duration):
    document = _alignment_document()
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 0.08, 2.0),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 2.1, 4.0),
    ]

    with pytest.raises(script_document.MarkdownAlignmentError, match="总时长"):
        script_document.build_timed_scenes(
            document,
            timed_rows,
            total_duration=total_duration,
        )


def test_build_timed_scenes_rejects_even_tiny_timeline_truncation():
    document = _alignment_document()
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 0.08, 8.56),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 8.88, 21.68),
    ]

    with pytest.raises(script_document.MarkdownAlignmentError, match="总时长"):
        script_document.build_timed_scenes(
            document,
            timed_rows,
            total_duration=21.6799995,
        )


def test_build_timed_scenes_allows_total_equal_to_last_voice_end():
    document = _alignment_document()
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 0.08, 8.56),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 8.88, 21.68),
    ]

    timed_scenes = script_document.build_timed_scenes(
        document,
        timed_rows,
        total_duration=21.68,
    )

    assert [(scene.start, scene.end) for scene in timed_scenes] == [
        (0.0, 8.88),
        (8.88, 21.68),
    ]


@pytest.mark.parametrize("total_duration", [True, False, "21.95"])
def test_build_timed_scenes_rejects_non_numeric_total_duration_types(
    total_duration,
):
    document = _alignment_document()
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 0.08, 0.25),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 0.30, 0.50),
    ]

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="int 或 float",
    ):
        script_document.build_timed_scenes(
            document,
            timed_rows,
            total_duration=total_duration,
        )


def test_build_timed_scenes_treats_explicit_none_as_legacy_timeline_mode():
    document = _alignment_document()
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 0.08, 0.25),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 0.30, 0.50),
    ]

    timed_scenes = script_document.build_timed_scenes(
        document,
        timed_rows,
        total_duration=None,
    )

    assert [(scene.start, scene.end) for scene in timed_scenes] == [
        (0.08, 0.25),
        (0.30, 0.50),
    ]


def test_build_timed_scenes_rejects_direct_documents_without_material_scenes():
    document = script_document.MarkdownScriptDocument(
        title="无素材场景",
        rows=[
            script_document.MarkdownScriptRow(
                number=1,
                text="这一行没有素材搜索词。",
                emphasis_terms=[],
                material_search_terms=[],
            )
        ],
    )
    timed_rows = [
        script_document.TimedScriptRow(
            number=1,
            text=document.rows[0].text,
            emphasis_terms=(),
            start=0.0,
            end=2.0,
        )
    ]

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="第一行必须提供素材搜索词",
    ):
        script_document.build_scenes(document)
    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="第一行必须提供素材搜索词",
    ):
        script_document.build_timed_scenes(
            document,
            timed_rows,
            total_duration=2.1,
        )


def test_build_timed_scenes_rejects_a_direct_document_whose_first_scene_starts_late():
    document = script_document.MarkdownScriptDocument(
        title="首场景缺失",
        rows=[
            script_document.MarkdownScriptRow(
                number=1,
                text="第一行没有素材搜索词。",
                emphasis_terms=[],
                material_search_terms=[],
            ),
            script_document.MarkdownScriptRow(
                number=2,
                text="第二行才提供素材搜索词。",
                emphasis_terms=[],
                material_search_terms=["wedding"],
            ),
        ],
    )
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 0.0, 1.0),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 1.0, 2.0),
    ]

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="第一行必须提供素材搜索词",
    ):
        script_document.build_timed_scenes(
            document,
            timed_rows,
            total_duration=2.1,
        )


def test_build_timed_scenes_rejects_non_increasing_scene_range():
    document = script_document.parse_markdown_script(VALID_MARKDOWN)
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 5.0, 6.0),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 1.0, 2.0),
        script_document.TimedScriptRow(3, document.rows[2].text, (), 6.0, 7.0),
    ]

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="场景 1.*结束时间必须晚于开始时间",
    ):
        script_document.build_timed_scenes(document, timed_rows)


def test_build_timed_scenes_rejects_duplicate_timed_row_numbers():
    document = _alignment_document()
    timed_rows = [
        script_document.TimedScriptRow(1, document.rows[0].text, (), 0.0, 2.0),
        script_document.TimedScriptRow(1, document.rows[0].text, (), 2.0, 3.0),
        script_document.TimedScriptRow(2, document.rows[1].text, (), 3.0, 5.0),
    ]

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="重复的第 1 行",
    ):
        script_document.build_timed_scenes(document, timed_rows)


def test_align_rows_reports_the_source_row_on_mismatch():
    document = _alignment_document()

    with pytest.raises(script_document.MarkdownAlignmentError, match="第 1 行"):
        script_document.align_rows_to_subtitles(
            document,
            [(1, "00:00:00,000 --> 00:00:01,000", "完全不同")],
        )


def test_align_rows_rejects_source_rows_without_visible_text():
    document = _alignment_document()
    document.rows[0].text = "，。！？"

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="第 1 行.*可见文本为空",
    ):
        script_document.align_rows_to_subtitles(
            document,
            [(1, "00:00:00,000 --> 00:00:01,000", "婚姻需要沟通")],
        )


def test_align_rows_handles_subtitles_without_visible_text_as_a_mismatch():
    document = _alignment_document()

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="第 1 行.*无法映射",
    ):
        script_document.align_rows_to_subtitles(
            document,
            [(1, "00:00:00,000 --> 00:00:01,000", "，。！？")],
        )


@pytest.mark.parametrize(
    "time_range",
    [
        "00:00:00 --> 00:00:01,000",
        "00:00:00,000 - 00:00:01,000",
        "00:00:01,000 --> 00:00:00,000",
        "00:61:00,000 --> 00:61:01,000",
    ],
)
def test_align_rows_rejects_malformed_srt_ranges(time_range):
    document = _alignment_document()

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="字幕 1.*时间范围",
    ):
        script_document.align_rows_to_subtitles(
            document,
            [(1, time_range, "婚姻需要沟通")],
        )


def test_build_timed_scenes_reports_missing_required_rows():
    document = _alignment_document()
    timed_rows = [
        script_document.TimedScriptRow(
            number=1,
            text=document.rows[0].text,
            emphasis_terms=("婚姻",),
            start=0.0,
            end=2.0,
        )
    ]

    with pytest.raises(
        script_document.MarkdownAlignmentError,
        match="场景 2.*第 2 行",
    ):
        script_document.build_timed_scenes(document, timed_rows)
