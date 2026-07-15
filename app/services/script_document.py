import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.models.script_document import MarkdownScriptDocument, MarkdownScriptRow


EXPECTED_COLUMNS = ("序号", "口播文案", "重点词", "素材搜索词")

_LEVEL_ONE_TITLE = re.compile(r"#\s+(.+?)\s*")
_LIST_SEPARATOR = re.compile(r"[；;]")
_TABLE_SEPARATOR = re.compile(r":?-{3,}:?")
_SEARCH_WORD = r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*"
_SAFE_ENGLISH_PHRASE = re.compile(
    rf"(?=.*[A-Za-z]){_SEARCH_WORD}(?: +(?:& +)?{_SEARCH_WORD})*"
)
_SRT_RANGE = re.compile(
    r"\s*(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})"
    r"\s*-->\s*"
    r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})\s*"
)


class MarkdownScriptError(ValueError):
    pass


class MarkdownAlignmentError(ValueError):
    pass


@dataclass(frozen=True)
class ScriptScene:
    index: int
    first_row: int
    last_row: int
    search_terms: tuple[str, ...]


@dataclass(frozen=True)
class TimedScriptRow:
    number: int
    text: str
    emphasis_terms: tuple[str, ...]
    start: float
    end: float


@dataclass(frozen=True)
class TimedScriptScene:
    index: int
    first_row: int
    last_row: int
    search_terms: tuple[str, ...]
    start: float
    end: float


def visible_text(value: str) -> str:
    return "".join(
        character
        for character in str(value or "")
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise MarkdownScriptError("表格行必须使用竖线包围")

    cells: list[str] = []
    current: list[str] = []
    index = 1
    final_delimiter = len(stripped) - 1
    while index < final_delimiter:
        character = stripped[index]
        if character == "\\" and index + 1 < final_delimiter:
            if stripped[index + 1] == "|":
                current.append("|")
                index += 2
                continue
        if character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _is_table_separator(cells: list[str]) -> bool:
    return len(cells) == len(EXPECTED_COLUMNS) and all(
        _TABLE_SEPARATOR.fullmatch(cell) for cell in cells
    )


def _split_cell_list(
    value: str,
    row_number: int,
    field_name: str,
    warnings: list[str],
) -> list[str]:
    items = [item.strip() for item in _LIST_SEPARATOR.split(value) if item.strip()]
    unique_items = list(dict.fromkeys(items))
    if len(unique_items) != len(items):
        warnings.append(
            f"第 {row_number} 行{field_name}存在重复项，已按原顺序去重"
        )
    return unique_items


def parse_markdown_script(markdown: str) -> MarkdownScriptDocument:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    title_matches = [
        (index, match.group(1).strip())
        for index, line in enumerate(lines)
        if (match := _LEVEL_ONE_TITLE.fullmatch(line))
    ]
    if len(title_matches) != 1 or not title_matches[0][1]:
        raise MarkdownScriptError("缺少一级标题或一级标题数量不是 1")

    header_indexes: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if tuple(_split_table_row(line)) == EXPECTED_COLUMNS:
                header_indexes.append(index)
    if len(header_indexes) != 1:
        raise MarkdownScriptError("缺少固定四列表格或表格数量不是 1")

    title_index, title = title_matches[0]
    header_index = header_indexes[0]
    if title_index >= header_index:
        raise MarkdownScriptError("Markdown 不能包含标题和表格之外的内容")
    for index, line in enumerate(lines[:header_index]):
        if line.strip() and index != title_index:
            raise MarkdownScriptError("Markdown 不能包含标题和表格之外的内容")

    separator_index = header_index + 1
    if separator_index >= len(lines):
        raise MarkdownScriptError("表格分隔行无效")
    separator_cells = _split_table_row(lines[separator_index])
    if not _is_table_separator(separator_cells):
        raise MarkdownScriptError("表格分隔行无效")

    rows: list[MarkdownScriptRow] = []
    warnings: list[str] = []
    table_ended = False
    for source_line in lines[separator_index + 1 :]:
        if not source_line.strip():
            table_ended = True
            continue
        if table_ended:
            raise MarkdownScriptError("Markdown 不能包含标题和表格之外的内容")
        if "|" not in source_line:
            raise MarkdownScriptError("Markdown 不能包含标题和表格之外的内容")

        cells = _split_table_row(source_line)
        if len(cells) != len(EXPECTED_COLUMNS):
            raise MarkdownScriptError("表格必须恰好包含四列")

        number_text, text, emphasis_text, search_text = cells
        expected_number = len(rows) + 1
        if not number_text.isdigit() or int(number_text) != expected_number:
            raise MarkdownScriptError("序号必须从 1 开始连续递增")
        if not text:
            raise MarkdownScriptError(f"第 {expected_number} 行口播文案不能为空")

        emphasis_terms = _split_cell_list(
            emphasis_text,
            expected_number,
            "重点词",
            warnings,
        )
        normalized_text = visible_text(text)
        for term in emphasis_terms:
            normalized_term = visible_text(term)
            if not normalized_term or normalized_term not in normalized_text:
                raise MarkdownScriptError(
                    f"第 {expected_number} 行重点词“{term}”不在口播文案中"
                )

        search_terms = _split_cell_list(
            search_text,
            expected_number,
            "素材搜索词",
            warnings,
        )
        if len(search_terms) > 3:
            raise MarkdownScriptError(
                f"第 {expected_number} 行素材搜索词不能超过 3 个"
            )
        for term in search_terms:
            if not _SAFE_ENGLISH_PHRASE.fullmatch(term):
                raise MarkdownScriptError(
                    f"第 {expected_number} 行素材搜索词必须使用英文"
                )
        if expected_number == 1 and not search_terms:
            raise MarkdownScriptError("第一行必须填写素材搜索词")

        rows.append(
            MarkdownScriptRow(
                number=expected_number,
                text=text,
                emphasis_terms=emphasis_terms,
                material_search_terms=search_terms,
            )
        )

    if not rows:
        raise MarkdownScriptError("口播表格不能为空")
    return MarkdownScriptDocument(title=title, rows=rows, warnings=warnings)


def parse_markdown_upload(
    filename: str,
    payload: bytes,
) -> MarkdownScriptDocument:
    if Path(filename).suffix.lower() != ".md":
        raise MarkdownScriptError("上传文件必须使用 .md 扩展名")
    try:
        markdown = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MarkdownScriptError("Markdown 必须使用 UTF-8 编码") from exc
    return parse_markdown_script(markdown)


def build_scenes(document: MarkdownScriptDocument) -> list[ScriptScene]:
    if not document.rows[0].material_search_terms:
        raise MarkdownAlignmentError("Markdown 第一行必须提供素材搜索词")

    scenes: list[ScriptScene] = []
    for row in document.rows:
        if not row.material_search_terms:
            continue
        if scenes:
            previous = scenes[-1]
            scenes[-1] = ScriptScene(
                index=previous.index,
                first_row=previous.first_row,
                last_row=row.number - 1,
                search_terms=previous.search_terms,
            )
        scenes.append(
            ScriptScene(
                index=len(scenes) + 1,
                first_row=row.number,
                last_row=row.number,
                search_terms=tuple(row.material_search_terms),
            )
        )

    if scenes:
        final = scenes[-1]
        scenes[-1] = ScriptScene(
            index=final.index,
            first_row=final.first_row,
            last_row=document.rows[-1].number,
            search_terms=final.search_terms,
        )
    return scenes


def _parse_srt_range(value: str) -> tuple[float, float]:
    match = _SRT_RANGE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise MarkdownAlignmentError(f"字幕时间范围无效：{value!r}")

    values = [int(part) for part in match.groups()]

    def seconds(parts: list[int]) -> float:
        hours, minutes, whole_seconds, milliseconds = parts
        return hours * 3600 + minutes * 60 + whole_seconds + milliseconds / 1000

    start = seconds(values[:4])
    end = seconds(values[4:])
    if end <= start:
        raise MarkdownAlignmentError(f"字幕时间范围必须递增：{value!r}")
    return start, end


def align_rows_to_subtitles(
    document: MarkdownScriptDocument,
    subtitles: list[tuple[int, str, str]],
) -> list[TimedScriptRow]:
    subtitle_spans: list[tuple[int, int, int, float, float]] = []
    joined = ""
    previous_end: float | None = None
    for subtitle_index, time_range, subtitle_text in subtitles:
        try:
            start, end = _parse_srt_range(time_range)
        except MarkdownAlignmentError as exc:
            raise MarkdownAlignmentError(
                f"字幕 {subtitle_index} 时间范围无效：{time_range!r}"
            ) from exc
        if previous_end is not None and start < previous_end:
            raise MarkdownAlignmentError(
                f"字幕 {subtitle_index} 与上一条字幕时间重叠或倒序："
                f"开始 {start:.3f}s，上一条结束 {previous_end:.3f}s"
            )
        previous_end = end

        normalized = visible_text(subtitle_text)
        if not normalized:
            continue
        span_start = len(joined)
        joined += normalized
        subtitle_spans.append(
            (subtitle_index, span_start, len(joined), start, end)
        )

    def time_for_offset(offset: int, *, use_end: bool) -> float:
        for _, span_start, span_end, start, end in subtitle_spans:
            if span_start <= offset < span_end:
                character_offset = offset - span_start + (1 if use_end else 0)
                ratio = character_offset / (span_end - span_start)
                mapped = start + (end - start) * ratio
                return min(end, max(start, mapped))
        raise MarkdownAlignmentError("字幕字符时间映射失败")

    cursor = 0
    timed_rows: list[TimedScriptRow] = []
    for row in document.rows:
        normalized = visible_text(row.text)
        if not normalized:
            raise MarkdownAlignmentError(
                f"第 {row.number} 行可见文本为空，无法映射到生成字幕"
            )

        start_offset = joined.find(normalized, cursor)
        if start_offset < 0:
            raise MarkdownAlignmentError(
                f"第 {row.number} 行无法映射到生成字幕：{row.text}"
            )
        end_offset = start_offset + len(normalized) - 1
        start = time_for_offset(start_offset, use_end=False)
        end = time_for_offset(end_offset, use_end=True)
        if end < start:
            raise MarkdownAlignmentError(
                f"第 {row.number} 行生成了无效字幕时间：{start} - {end}"
            )
        timed_rows.append(
            TimedScriptRow(
                number=row.number,
                text=row.text,
                emphasis_terms=tuple(row.emphasis_terms),
                start=start,
                end=end,
            )
        )
        cursor = end_offset + 1
    return timed_rows


def build_timed_scenes(
    document: MarkdownScriptDocument,
    timed_rows: list[TimedScriptRow],
    total_duration: float | None = None,
) -> list[TimedScriptScene]:
    rows_by_number: dict[int, TimedScriptRow] = {}
    for row in timed_rows:
        if row.number in rows_by_number:
            raise MarkdownAlignmentError(f"字幕时间包含重复的第 {row.number} 行")
        rows_by_number[row.number] = row

    scenes = build_scenes(document)
    for scene in scenes:
        missing_rows = [
            row_number
            for row_number in range(scene.first_row, scene.last_row + 1)
            if row_number not in rows_by_number
        ]
        if missing_rows:
            raise MarkdownAlignmentError(
                f"场景 {scene.index} 缺少第 {missing_rows[0]} 行的字幕时间"
            )

    normalized_total_duration = None
    if total_duration is not None:
        if isinstance(total_duration, bool) or not isinstance(
            total_duration,
            (int, float),
        ):
            raise MarkdownAlignmentError(
                "视频总时长必须是 int 或 float 数值，且不能是 bool"
            )
        try:
            normalized_total_duration = float(total_duration)
        except (OverflowError, TypeError, ValueError) as exc:
            raise MarkdownAlignmentError("视频总时长必须是有效数字") from exc
        if (
            not math.isfinite(normalized_total_duration)
            or normalized_total_duration <= 0
        ):
            raise MarkdownAlignmentError("视频总时长必须是有限的正数")

        last_voice_end = rows_by_number[scenes[-1].last_row].end
        if not math.isfinite(last_voice_end):
            raise MarkdownAlignmentError("最后一行字幕结束时间必须是有限数字")
        if normalized_total_duration < last_voice_end:
            raise MarkdownAlignmentError(
                "视频总时长不得短于最后一行字幕结束时间："
                f"{normalized_total_duration} < {last_voice_end}"
            )

    timed_scenes: list[TimedScriptScene] = []
    for scene_offset, scene in enumerate(scenes):
        if normalized_total_duration is None:
            scene_start = rows_by_number[scene.first_row].start
            scene_end = rows_by_number[scene.last_row].end
        else:
            scene_start = (
                0.0
                if scene_offset == 0
                else rows_by_number[scene.first_row].start
            )
            scene_end = (
                normalized_total_duration
                if scene_offset == len(scenes) - 1
                else rows_by_number[scenes[scene_offset + 1].first_row].start
            )
        if not math.isfinite(scene_start) or not math.isfinite(scene_end):
            raise MarkdownAlignmentError(
                f"场景 {scene.index} 时间必须是有限数字：{scene_start} - {scene_end}"
            )
        if scene_end <= scene_start:
            raise MarkdownAlignmentError(
                f"场景 {scene.index} 结束时间必须晚于开始时间："
                f"{scene_start} - {scene_end}"
            )
        timed_scenes.append(
            TimedScriptScene(
                index=scene.index,
                first_row=scene.first_row,
                last_row=scene.last_row,
                search_terms=scene.search_terms,
                start=scene_start,
                end=scene_end,
            )
        )
    return timed_scenes


def apply_to_video_params(document: MarkdownScriptDocument, params) -> None:
    """把 Markdown 内容映射到任务参数，同时保留用户的生成设置。"""
    params.video_subject = document.title
    params.video_script = document.script_text()
    params.video_terms = [
        term
        for scene in build_scenes(document)
        for term in scene.search_terms
    ]
    params.markdown_script = document
