from __future__ import annotations

import re


_DIGITS = "零一二三四五六七八九"
_SENTENCE_ENDINGS = set("。！？!?；;\n")
_UNITS = ((1000, "千"), (100, "百"), (10, "十"))


def _integer_to_chinese(value: int) -> str:
    if value == 0:
        return _DIGITS[0]
    if value < 0:
        return "负" + _integer_to_chinese(-value)
    if value >= 10000:
        return "".join(_DIGITS[int(digit)] for digit in str(value))

    result: list[str] = []
    remainder = value
    zero_pending = False
    for unit_value, unit_name in _UNITS:
        digit, remainder = divmod(remainder, unit_value)
        if digit:
            if zero_pending and result:
                result.append("零")
            if not (unit_value == 10 and digit == 1 and not result):
                result.append(_DIGITS[digit])
            result.append(unit_name)
            zero_pending = False
        elif result and remainder:
            zero_pending = True
    if remainder:
        if zero_pending:
            result.append("零")
        result.append(_DIGITS[remainder])
    return "".join(result)


def _decimal_to_chinese(value: str) -> str:
    integer, _, fraction = value.partition(".")
    if not fraction:
        return _integer_to_chinese(int(integer))
    return f"{_integer_to_chinese(int(integer))}点{''.join(_DIGITS[int(d)] for d in fraction)}"


def _normalize_display_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.strip())


def normalize_narration(text: str) -> tuple[str, str]:
    display_text = _normalize_display_text(text)
    if not display_text:
        raise ValueError("text must not be empty")

    spoken_text = display_text
    spoken_text = re.sub(
        r"(\d{4})(\s*年)",
        lambda match: "".join(_DIGITS[int(digit)] for digit in match.group(1)) + match.group(2),
        spoken_text,
    )
    spoken_text = re.sub(
        r"￥\s*(\d+(?:\.\d+)?)",
        lambda match: _decimal_to_chinese(match.group(1)) + "元",
        spoken_text,
    )
    spoken_text = re.sub(
        r"(\d+(?:\.\d+)?)\s*%",
        lambda match: "百分之" + _decimal_to_chinese(match.group(1)),
        spoken_text,
    )
    spoken_text = re.sub(r"(?<![A-Za-z])AI(?![A-Za-z])", "A I", spoken_text)
    spoken_text = re.sub(
        r"(?<![\w])\d+\.\d+(?![\w])",
        lambda match: _decimal_to_chinese(match.group(0)),
        spoken_text,
    )
    spoken_text = re.sub(
        r"(?<![\w])\d+(?![\w])",
        lambda match: _integer_to_chinese(int(match.group(0))),
        spoken_text,
    )
    return spoken_text, display_text


def split_into_blocks(spoken_text: str, max_chars: int = 100) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    text = spoken_text.strip()
    if not text:
        raise ValueError("spoken_text must not be empty")

    blocks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        boundary = max(
            (index + 1 for index, char in enumerate(remaining[:max_chars]) if char in _SENTENCE_ENDINGS),
            default=0,
        )
        cut_at = boundary or max_chars
        blocks.append(remaining[:cut_at])
        remaining = remaining[cut_at:]
    if remaining:
        blocks.append(remaining)
    return blocks
