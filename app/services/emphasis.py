from __future__ import annotations

import json
import random
import re
import unicodedata
import wave
from dataclasses import dataclass, replace
from pathlib import Path

from loguru import logger


EMPHASIS_COLORS = (
    "#FFB000",
    "#FF5A36",
    "#A86BFF",
    "#22C7A8",
    "#FF5FA2",
    "#FFD43B",
)
EMPHASIS_ANIMATIONS = (
    "pop",
    "slide_left",
    "slide_right",
    "zoom",
    "shake",
    "stamp",
)
_SOUND_IDS_BY_GROUP = {
    "pop": ("pop-01", "pop-02", "pop-03"),
    "whoosh": ("whoosh-01", "whoosh-02", "whoosh-03"),
    "hit": ("hit-01", "hit-02", "hit-03"),
    "sparkle": ("sparkle-01", "sparkle-02", "sparkle-03"),
}
EMPHASIS_SOUND_IDS = tuple(
    sound_id for sound_ids in _SOUND_IDS_BY_GROUP.values() for sound_id in sound_ids
)
_ANIMATION_SOUND_GROUPS = {
    "pop": ("pop", "sparkle"),
    "slide_left": ("whoosh",),
    "slide_right": ("whoosh",),
    "zoom": ("hit",),
    "shake": ("pop",),
    "stamp": ("hit",),
}
EMPHASIS_POSITIONS = ("left", "center", "right")
_DEFAULT_COLOR = "#FF5A36"
_DEFAULT_ANIMATION = "pop"
_MIN_DURATION = 0.45
_MAX_DURATION = 1.35


def _visible_text(value: str) -> str:
    return "".join(
        char
        for char in str(value or "")
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def _timestamp_to_seconds(value: str) -> float:
    timestamp = value.strip().replace(",", ".")
    hours, minutes, seconds = timestamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt_time_range(value: str) -> tuple[float, float]:
    try:
        start, end = (part.strip() for part in value.split("-->", 1))
        parsed_start = _timestamp_to_seconds(start)
        parsed_end = _timestamp_to_seconds(end)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid SRT time range: {value!r}") from exc
    if parsed_end <= parsed_start:
        raise ValueError(f"SRT time range must increase: {value!r}")
    return parsed_start, parsed_end


def parse_manual_terms(value: str) -> list[str]:
    return list(
        dict.fromkeys(
            item.strip()
            for item in re.split(r"[,，\n]", str(value or ""))
            if item.strip()
        )
    )


def _normalized_terms(terms: list[str]) -> list[str]:
    normalized: list[str] = []
    for term in terms:
        value = _visible_text(term)
        if len(value) < 2:
            continue
        if value not in normalized:
            normalized.append(value)
    return normalized


def _fallback_terms(subtitles: list[tuple[int, str, str]]) -> list[str]:
    terms: list[str] = []
    for _, _, text in subtitles:
        visible = _visible_text(text)
        if len(visible) >= 2:
            terms.append(visible[:6])
    return _normalized_terms(terms)


def _validated_source_row_number(value: object) -> int:
    if type(value) is not int:
        raise ValueError("emphasis source row number must be an integer")
    if value != -1 and value < 1:
        raise ValueError("emphasis source row number must be -1 or a positive integer")
    return value


@dataclass(frozen=True)
class EmphasisCue:
    text: str
    start: float
    end: float
    color: str
    animation: str
    sound_id: str
    position: str
    subtitle_index: int = -1
    layer: int = 0
    source_row_number: int = -1

    def __post_init__(self) -> None:
        text = _visible_text(self.text)
        if len(text) < 2:
            raise ValueError("emphasis text must contain at least two visible characters")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("emphasis cue timing is invalid")
        if self.color not in EMPHASIS_COLORS:
            raise ValueError(f"unsupported emphasis color: {self.color}")
        if self.animation not in EMPHASIS_ANIMATIONS:
            raise ValueError(f"unsupported emphasis animation: {self.animation}")
        if self.sound_id not in EMPHASIS_SOUND_IDS:
            raise ValueError(f"unsupported emphasis sound: {self.sound_id}")
        if self.position not in EMPHASIS_POSITIONS:
            raise ValueError(f"unsupported emphasis position: {self.position}")
        if self.subtitle_index < -1:
            raise ValueError("emphasis subtitle index must be -1 or greater")
        _validated_source_row_number(self.source_row_number)
        if self.layer not in {0, 1, 2}:
            raise ValueError(f"unsupported emphasis layer: {self.layer}")
        object.__setattr__(self, "text", text)

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "color": self.color,
            "animation": self.animation,
            "sound_id": self.sound_id,
            "position": self.position,
            "subtitle_index": self.subtitle_index,
            "layer": self.layer,
            "source_row_number": self.source_row_number,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "EmphasisCue":
        return cls(
            text=str(payload["text"]),
            start=float(payload["start"]),
            end=float(payload["end"]),
            color=str(payload["color"]),
            animation=str(payload["animation"]),
            sound_id=str(payload["sound_id"]),
            position=str(payload["position"]),
            subtitle_index=int(payload.get("subtitle_index", -1)),
            layer=int(payload.get("layer", 0)),
            source_row_number=_validated_source_row_number(
                payload.get("source_row_number", -1)
            ),
        )


@dataclass(frozen=True)
class SoundManifestItem:
    sound_id: str
    group: str
    file: str
    duration: float

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SoundManifestItem":
        item = cls(
            sound_id=str(payload["sound_id"]),
            group=str(payload["group"]),
            file=str(payload["file"]),
            duration=float(payload["duration"]),
        )
        if item.sound_id not in EMPHASIS_SOUND_IDS:
            raise ValueError(f"unsupported emphasis sound: {item.sound_id}")
        if item.group not in _SOUND_IDS_BY_GROUP:
            raise ValueError(f"unsupported emphasis sound group: {item.group}")
        if item.sound_id not in _SOUND_IDS_BY_GROUP[item.group]:
            raise ValueError(f"sound {item.sound_id!r} does not belong to {item.group!r}")
        if not item.file or Path(item.file).is_absolute() or ".." in Path(item.file).parts:
            raise ValueError("sound manifest file must be a safe relative path")
        if item.duration <= 0:
            raise ValueError("sound manifest duration must be positive")
        return item


def sound_group_for_animation(animation: str, sound_id: str) -> str:
    if animation not in _ANIMATION_SOUND_GROUPS:
        raise ValueError(f"unsupported emphasis animation: {animation}")
    for group in _ANIMATION_SOUND_GROUPS[animation]:
        if sound_id in _SOUND_IDS_BY_GROUP[group]:
            return group
    raise ValueError(f"sound {sound_id!r} does not match animation {animation!r}")


def _style_for(
    task_id: str,
    subtitle_index: int,
    term_index: int,
    *,
    random_colors: bool,
    random_animations: bool,
) -> tuple[str, str, str, str]:
    selector = random.Random(f"{task_id}:{subtitle_index}:{term_index}")
    color = selector.choice(EMPHASIS_COLORS) if random_colors else _DEFAULT_COLOR
    animation = (
        selector.choice(EMPHASIS_ANIMATIONS)
        if random_animations
        else _DEFAULT_ANIMATION
    )
    group = selector.choice(_ANIMATION_SOUND_GROUPS[animation])
    sound_id = selector.choice(_SOUND_IDS_BY_GROUP[group])
    position = selector.choice(EMPHASIS_POSITIONS)
    return color, animation, sound_id, position


def _cue_for_term(
    task_id: str,
    subtitle_index: int,
    term_index: int,
    time_range: str,
    subtitle_text: str,
    term: str,
    *,
    random_colors: bool,
    random_animations: bool,
) -> EmphasisCue | None:
    visible_subtitle = _visible_text(subtitle_text)
    start_index = visible_subtitle.find(term)
    if start_index < 0:
        return None
    cue_start, cue_end = parse_srt_time_range(time_range)
    ratio = (cue_end - cue_start) / len(visible_subtitle)
    start = cue_start + start_index * ratio
    end = cue_end
    if end <= start:
        return None
    color, animation, sound_id, position = _style_for(
        task_id,
        subtitle_index,
        term_index,
        random_colors=random_colors,
        random_animations=random_animations,
    )
    return EmphasisCue(
        text=term,
        start=start,
        end=end,
        color=color,
        animation=animation,
        sound_id=sound_id,
        position=position,
        subtitle_index=subtitle_index,
    )


def _arrange_grouped_cues(
    task_id: str, cues: list[EmphasisCue]
) -> list[EmphasisCue]:
    grouped: dict[tuple[str, int], list[EmphasisCue]] = {}
    for cue in cues:
        group_key = (
            ("row", cue.source_row_number)
            if cue.source_row_number > 0
            else ("subtitle", cue.subtitle_index)
        )
        grouped.setdefault(group_key, []).append(cue)

    arranged: list[EmphasisCue] = []
    for group_key, group in grouped.items():
        ordered = sorted(group, key=lambda item: (item.start, item.text))
        positions = list(EMPHASIS_POSITIONS)
        random.Random(f"{task_id}:{group_key[1]}:positions").shuffle(positions)
        layer_entries: dict[int, int] = {}
        for order, cue in enumerate(ordered):
            layer = order % len(positions)
            if layer in layer_entries:
                previous_index = layer_entries[layer]
                previous = arranged[previous_index]
                arranged[previous_index] = replace(previous, end=cue.start)
            arranged.append(replace(cue, layer=layer, position=positions[layer]))
            layer_entries[layer] = len(arranged) - 1

    return sorted(arranged, key=lambda item: (item.start, item.layer, item.text))


def build_markdown_emphasis_cues(
    task_id: str,
    timed_rows,
    random_colors: bool = True,
    random_animations: bool = True,
) -> list[EmphasisCue]:
    cues: list[EmphasisCue] = []
    term_index = 0
    for row in timed_rows:
        visible_row = _visible_text(row.text)
        if not visible_row:
            raise ValueError(f"第 {row.number} 行可见文本为空，无法生成重点词时间")
        if row.end <= row.start:
            raise ValueError(f"第 {row.number} 行时间范围无效")

        seconds_per_character = (row.end - row.start) / len(visible_row)
        for term in row.emphasis_terms:
            visible_term = _visible_text(term)
            if not visible_term:
                raise ValueError(f"第 {row.number} 行重点词归一化后为空")
            start_index = visible_row.find(visible_term)
            if start_index < 0:
                raise ValueError(
                    f"第 {row.number} 行重点词“{term}”无法映射到字幕时间"
                )
            color, animation, sound_id, position = _style_for(
                task_id,
                row.number,
                term_index,
                random_colors=random_colors,
                random_animations=random_animations,
            )
            cues.append(
                EmphasisCue(
                    text=visible_term,
                    start=row.start + start_index * seconds_per_character,
                    end=row.end,
                    color=color,
                    animation=animation,
                    sound_id=sound_id,
                    position=position,
                    subtitle_index=-1,
                    source_row_number=row.number,
                )
            )
            term_index += 1

    return _arrange_grouped_cues(task_id, cues)


def build_emphasis_cues(
    task_id: str,
    subtitles: list[tuple[int, str, str]],
    automatic_terms: list[str],
    manual_terms: str = "",
    random_colors: bool = True,
    random_animations: bool = True,
) -> list[EmphasisCue]:
    terms = _normalized_terms(parse_manual_terms(manual_terms))
    if not terms:
        terms = _normalized_terms(automatic_terms) or _fallback_terms(subtitles)

    cues: list[EmphasisCue] = []
    for term_index, term in enumerate(terms):
        for subtitle_index, time_range, subtitle_text in subtitles:
            cue = _cue_for_term(
                task_id,
                subtitle_index,
                term_index,
                time_range,
                subtitle_text,
                term,
                random_colors=random_colors,
                random_animations=random_animations,
            )
            if cue is not None:
                cues.append(cue)
                break
        else:
            logger.warning(f"skip unmatched emphasis term: {term}")

    return _arrange_grouped_cues(task_id, cues)


def write_emphasis_cues(cues: list[EmphasisCue], output_path: str | Path) -> None:
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([cue.to_dict() for cue in cues], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_emphasis_cues(path: str | Path) -> list[EmphasisCue]:
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("emphasis cue file must contain a JSON list")
    return [EmphasisCue.from_dict(item) for item in payload]


def load_sound_manifest(project_root: str | Path) -> list[SoundManifestItem]:
    source = Path(project_root) / "resource" / "sfx" / "manifest.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    sounds = payload.get("sounds") if isinstance(payload, dict) else None
    if not isinstance(sounds, list):
        raise ValueError("sound manifest must contain a sounds list")
    return [SoundManifestItem.from_dict(item) for item in sounds]


def read_wav_duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        if frame_rate <= 0:
            raise ValueError(f"WAV has invalid sample rate: {path}")
        return wav_file.getnframes() / frame_rate
