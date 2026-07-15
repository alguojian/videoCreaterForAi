import pytest

from app.services.local_voice.text_normalizer import normalize_narration, split_into_blocks


def test_normalize_narration_keeps_display_text_and_expands_spoken_text():
    display_text = "2026 年，AI 工具价格为 ￥99，转化率达到 3.5%。"

    spoken_text, returned_display = normalize_narration(display_text)

    assert returned_display == display_text
    assert spoken_text == "二零二六 年，A I 工具价格为 九十九元，转化率达到 百分之三点五。"


def test_normalize_narration_rejects_blank_input():
    with pytest.raises(ValueError, match="text must not be empty"):
        normalize_narration("  \n\t")


def test_split_into_blocks_preserves_all_text_and_prefers_sentence_boundaries():
    text = "第一句内容。第二句内容！第三句内容？第四句内容。"

    blocks = split_into_blocks(text, max_chars=8)

    assert "".join(blocks) == text
    assert all(0 < len(block) <= 8 for block in blocks)
    assert blocks[:2] == ["第一句内容。", "第二句内容！"]


def test_split_into_blocks_rejects_invalid_limit():
    with pytest.raises(ValueError, match="max_chars must be positive"):
        split_into_blocks("测试", max_chars=0)
