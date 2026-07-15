from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from moviepy import AudioFileClip, VideoFileClip


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import config
from app.models.schema import VideoAspect, VideoParams
from app.services import (
    llm,
    script_document,
    task,
    upload_post,
    video,
    voice,
)
from app.services import local_voice as local_voice_service


MARKDOWN_FILE = PROJECT_ROOT / "examples" / "markdown-scripts" / "marriage.md"
DEFAULT_TASK_ID = "marriage-markdown-landscape-test"
TASK_ID_ENV = "MPT_MARKDOWN_ACCEPTANCE_TASK_ID"
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
EXPECTED_ACCEPTANCE_CUE_COUNT = 12
MEDIA_DURATION_TOLERANCE_SECONDS = 0.02
TAIL_FRAME_OFFSET_SECONDS = 0.1
MIN_TAIL_BRIGHTNESS = 20.0


@dataclass(frozen=True)
class SceneValidationSummary:
    scene_count: int
    last_end: float
    material_count: int


@dataclass(frozen=True)
class EmphasisValidationSummary:
    cue_count: int
    repeated_term_rows: dict[str, list[int]]
    multi_cue_rows: list[int]


@dataclass(frozen=True)
class MediaValidationSummary:
    audio_duration: float
    combined_duration: float
    final_duration: float
    combined_narration_tail_brightness: float
    combined_media_tail_brightness: float
    final_narration_tail_brightness: float
    final_media_tail_brightness: float


@dataclass(frozen=True)
class AcceptanceArtifacts:
    final_video: Path
    combined_video: Path
    emphasis_path: Path
    scene_manifest: Path
    script_manifest: Path
    audio_file: Path
    scene_count: int
    material_count: int
    cue_count: int
    audio_duration: float
    combined_duration: float
    final_duration: float
    combined_narration_tail_brightness: float
    combined_media_tail_brightness: float
    final_narration_tail_brightness: float
    final_media_tail_brightness: float


class _MediaAcceptanceError(RuntimeError):
    pass


def _task_id() -> str:
    task_id = os.environ.get(TASK_ID_ENV, DEFAULT_TASK_ID).strip()
    if not _SAFE_TASK_ID.fullmatch(task_id):
        raise ValueError(f"{TASK_ID_ENV} must match {_SAFE_TASK_ID.pattern!r}")
    return task_id


def _reset_task_directory(task_id: str) -> Path:
    tasks_root = (PROJECT_ROOT / "storage" / "tasks").resolve()
    task_directory = (tasks_root / task_id).resolve()
    if task_directory.parent != tasks_root:
        raise ValueError("acceptance task directory escaped storage/tasks")
    if task_directory.exists():
        shutil.rmtree(task_directory)
    task_directory.mkdir(parents=True, exist_ok=True)
    return task_directory


def _redact_configured_secrets(message: object) -> str:
    redacted = str(message)
    for key in config.app.get("pixabay_api_keys", []):
        if key:
            redacted = redacted.replace(str(key), "[REDACTED]")
    return redacted


def load_acceptance_document():
    return script_document.parse_markdown_script(
        MARKDOWN_FILE.read_text(encoding="utf-8-sig")
    )


def require_task_artifact(
    task_directory: str | Path,
    candidate: str | Path,
    label: str,
) -> Path:
    task_root = Path(task_directory).expanduser().resolve()
    artifact = Path(candidate).expanduser().resolve()
    try:
        artifact.relative_to(task_root)
    except ValueError as exc:
        raise RuntimeError(
            f"{label} is outside current task directory: {artifact}"
        ) from exc
    if not artifact.is_file():
        raise RuntimeError(f"{label} does not exist: {artifact}")
    if artifact.stat().st_size <= 0:
        raise RuntimeError(f"{label} is empty: {artifact}")
    return artifact


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise RuntimeError(f"{label} must be finite")
    return normalized


def _read_json_artifact(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON: {path}") from exc


def _positive_media_duration(value: object, label: str) -> float:
    duration = _finite_number(value, f"{label} duration")
    if duration <= 0:
        raise _MediaAcceptanceError(f"{label} duration must be positive")
    return duration


def _tail_brightness(frame: object, label: str) -> float:
    pixels = np.asarray(frame, dtype=float)
    if pixels.ndim < 3 or pixels.shape[-1] < 3 or pixels.size == 0:
        raise _MediaAcceptanceError(f"{label} returned an invalid tail frame")
    rgb = pixels[..., :3]
    if not np.isfinite(rgb).all():
        raise _MediaAcceptanceError(f"{label} tail frame contains invalid pixels")
    luma = (
        0.2126 * rgb[..., 0]
        + 0.7152 * rgb[..., 1]
        + 0.0722 * rgb[..., 2]
    )
    return float(np.mean(luma))


def _safe_tail_time(duration: float) -> float:
    return max(0.0, min(duration - TAIL_FRAME_OFFSET_SECONDS, duration - 0.001))


def validate_media_outputs(
    audio_file: str | Path,
    combined_video: str | Path,
    final_video: str | Path,
) -> MediaValidationSummary:
    try:
        with (
            AudioFileClip(str(audio_file)) as narration,
            VideoFileClip(str(combined_video)) as combined,
            VideoFileClip(str(final_video)) as final,
        ):
            audio_duration = _positive_media_duration(
                narration.duration,
                "narration audio",
            )
            combined_duration = _positive_media_duration(
                combined.duration,
                "combined video",
            )
            final_duration = _positive_media_duration(
                final.duration,
                "final video",
            )

            narration_probe_time = max(
                0.0,
                min(audio_duration - 0.001, TAIL_FRAME_OFFSET_SECONDS),
            )
            narration.get_frame(narration_probe_time)

            for label, duration in (
                ("combined video", combined_duration),
                ("final video", final_duration),
            ):
                if duration + MEDIA_DURATION_TOLERANCE_SECONDS < audio_duration:
                    raise _MediaAcceptanceError(
                        f"{label} is shorter than narration audio: "
                        f"{duration:.3f}s < {audio_duration:.3f}s"
                    )

            narration_tail_time = _safe_tail_time(audio_duration)
            combined_narration_brightness = _tail_brightness(
                combined.get_frame(narration_tail_time),
                "combined video narration-tail",
            )
            combined_media_brightness = _tail_brightness(
                combined.get_frame(_safe_tail_time(combined_duration)),
                "combined video media-tail",
            )
            final_narration_brightness = _tail_brightness(
                final.get_frame(narration_tail_time),
                "final video narration-tail",
            )
            final_media_brightness = _tail_brightness(
                final.get_frame(_safe_tail_time(final_duration)),
                "final video media-tail",
            )
            for label, brightness in (
                (
                    "combined video narration-tail",
                    combined_narration_brightness,
                ),
                ("combined video media-tail", combined_media_brightness),
                ("final video narration-tail", final_narration_brightness),
                ("final video media-tail", final_media_brightness),
            ):
                if brightness < MIN_TAIL_BRIGHTNESS:
                    raise _MediaAcceptanceError(
                        f"{label} has a near-black narration tail frame: "
                        f"brightness={brightness:.3f}"
                    )

            if final.audio is None:
                raise _MediaAcceptanceError("final video has no audio stream")
            final_audio_duration = _positive_media_duration(
                final.audio.duration,
                "final audio stream",
            )
            if (
                final_audio_duration + MEDIA_DURATION_TOLERANCE_SECONDS
                < audio_duration
            ):
                raise _MediaAcceptanceError(
                    "final audio stream is shorter than narration audio: "
                    f"{final_audio_duration:.3f}s < {audio_duration:.3f}s"
                )
            final_audio_probe_time = max(
                0.0,
                min(narration_tail_time, final_audio_duration - 0.001),
            )
            try:
                final.audio.get_frame(final_audio_probe_time)
            except Exception as exc:
                raise _MediaAcceptanceError(
                    "final audio stream is unreadable"
                ) from exc

            return MediaValidationSummary(
                audio_duration=audio_duration,
                combined_duration=combined_duration,
                final_duration=final_duration,
                combined_narration_tail_brightness=(
                    combined_narration_brightness
                ),
                combined_media_tail_brightness=combined_media_brightness,
                final_narration_tail_brightness=final_narration_brightness,
                final_media_tail_brightness=final_media_brightness,
            )
    except _MediaAcceptanceError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "unable to read media for Markdown acceptance: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def validate_scene_manifest(
    document,
    manifest: object,
    task_directory: str | Path,
    audio_file: str | Path,
) -> SceneValidationSummary:
    expected_scenes = script_document.build_scenes(document)
    if len(expected_scenes) != 3:
        raise RuntimeError(
            f"acceptance document must build exactly 3 scenes, got {len(expected_scenes)}"
        )
    if not isinstance(manifest, list) or len(manifest) != len(expected_scenes):
        actual_count = len(manifest) if isinstance(manifest, list) else 0
        raise RuntimeError(
            f"scene manifest must contain exactly 3 scenes, got {actual_count}"
        )

    verified_audio = require_task_artifact(
        task_directory, audio_file, "narration audio"
    )
    audio_duration = _finite_number(
        voice.get_audio_duration(str(verified_audio)), "audio duration"
    )
    if audio_duration <= 0:
        raise RuntimeError("audio duration must be positive")
    expected_last_end = video.get_required_video_duration(audio_duration)

    previous_end: float | None = None
    material_count = 0
    last_end = 0.0
    for entry, expected in zip(manifest, expected_scenes, strict=True):
        if not isinstance(entry, dict):
            raise RuntimeError(f"scene {expected.index} manifest entry must be an object")
        if entry.get("scene_index") != expected.index:
            raise RuntimeError(f"scene {expected.index} index does not match document")
        if (
            entry.get("first_row") != expected.first_row
            or entry.get("last_row") != expected.last_row
        ):
            raise RuntimeError(
                f"scene {expected.index} row range does not match document"
            )
        if entry.get("search_terms") != list(expected.search_terms):
            raise RuntimeError(
                f"scene {expected.index} search_terms do not match document"
            )

        start = _finite_number(entry.get("start"), f"scene {expected.index} start")
        end = _finite_number(entry.get("end"), f"scene {expected.index} end")
        required = _finite_number(
            entry.get("required_duration"),
            f"scene {expected.index} required_duration",
        )
        if end <= start:
            raise RuntimeError(f"scene {expected.index} end must be after start")
        if expected.index == 1 and start != 0.0:
            raise RuntimeError("first scene must start at 0")
        if previous_end is not None and not math.isclose(
            start, previous_end, rel_tol=0.0, abs_tol=1e-6
        ):
            raise RuntimeError("scene timeline must be continuous")
        if not math.isclose(required, end - start, rel_tol=0.0, abs_tol=1e-6):
            raise RuntimeError(
                f"scene {expected.index} required_duration does not equal end-start"
            )

        video_paths = entry.get("video_paths")
        if not isinstance(video_paths, list) or not video_paths:
            raise RuntimeError(f"scene {expected.index} has no material video_paths")
        for material_path in video_paths:
            require_task_artifact(
                task_directory,
                material_path,
                f"scene {expected.index} material",
            )
            material_count += 1
        previous_end = end
        last_end = end

    if not math.isclose(
        last_end,
        expected_last_end,
        rel_tol=0.0,
        abs_tol=0.02,
    ):
        raise RuntimeError(
            "scene timeline does not end at required audio duration: "
            f"{last_end} != {expected_last_end}"
        )
    return SceneValidationSummary(
        scene_count=len(manifest),
        last_end=last_end,
        material_count=material_count,
    )


def validate_emphasis_manifest(
    document,
    cues: object,
) -> EmphasisValidationSummary:
    expected_pairs = [
        (term, row.number)
        for row in document.rows
        for term in row.emphasis_terms
    ]
    if len(expected_pairs) != EXPECTED_ACCEPTANCE_CUE_COUNT:
        raise RuntimeError(
            "acceptance document must define exactly "
            f"{EXPECTED_ACCEPTANCE_CUE_COUNT} emphasis terms, got "
            f"{len(expected_pairs)}"
        )
    if (
        not isinstance(cues, list)
        or len(cues) != EXPECTED_ACCEPTANCE_CUE_COUNT
    ):
        actual_count = len(cues) if isinstance(cues, list) else 0
        raise RuntimeError(
            "emphasis manifest must contain exactly "
            f"{EXPECTED_ACCEPTANCE_CUE_COUNT} cues, got {actual_count}"
        )

    actual_pairs: list[tuple[str, int]] = []
    row_counts: Counter[int] = Counter()
    term_rows: dict[str, set[int]] = {}
    for cue in cues:
        if not isinstance(cue, dict):
            raise RuntimeError("emphasis cue must be an object")
        text = cue.get("text")
        source_row = cue.get("source_row_number")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("emphasis cue text must be non-empty")
        if type(source_row) is not int or source_row < 1:
            raise RuntimeError("emphasis cue source row must be a positive integer")
        if not isinstance(cue.get("sound_id"), str) or not cue["sound_id"].strip():
            raise RuntimeError("emphasis cue sound_id must be non-empty")
        actual_pairs.append((text, source_row))
        row_counts[source_row] += 1
        term_rows.setdefault(text, set()).add(source_row)

    if Counter(actual_pairs) != Counter(expected_pairs):
        raise RuntimeError("emphasis cues do not match row-bound Markdown terms")

    repeated_term_rows = {
        term: sorted(rows)
        for term, rows in term_rows.items()
        if len(rows) >= 2
    }
    if repeated_term_rows.get("彼此") != [4, 6]:
        raise RuntimeError("emphasis term 彼此 must be bound to rows 4 and 6")
    multi_cue_rows = sorted(row for row, count in row_counts.items() if count >= 2)
    if not multi_cue_rows:
        raise RuntimeError("emphasis manifest must contain multiple cues in one row")
    return EmphasisValidationSummary(
        cue_count=len(cues),
        repeated_term_rows=repeated_term_rows,
        multi_cue_rows=multi_cue_rows,
    )


def validate_script_manifest(document, payload: object) -> None:
    markdown_payload = None
    if isinstance(payload, dict) and isinstance(payload.get("params"), dict):
        markdown_payload = payload["params"].get("markdown_script")
    if markdown_payload != document.model_dump(mode="json"):
        raise RuntimeError("script.json Markdown document does not match input document")


def validate_task_outputs(
    document,
    task_directory: str | Path,
    result: object,
) -> AcceptanceArtifacts:
    if not isinstance(result, dict):
        raise RuntimeError("Markdown sample did not return a task result")
    videos = result.get("videos")
    combined_videos = result.get("combined_videos")
    if not isinstance(videos, list) or len(videos) != 1:
        raise RuntimeError("Markdown sample must produce exactly one final video")
    if not isinstance(combined_videos, list) or len(combined_videos) != 1:
        raise RuntimeError("Markdown sample must produce exactly one combined video")

    final_video = require_task_artifact(
        task_directory, videos[0], "final video"
    )
    combined_video = require_task_artifact(
        task_directory, combined_videos[0], "combined video"
    )
    emphasis_path = require_task_artifact(
        task_directory, result.get("emphasis_path", ""), "emphasis manifest"
    )
    audio_file = require_task_artifact(
        task_directory, result.get("audio_file", ""), "narration audio"
    )
    scene_manifest = require_task_artifact(
        task_directory,
        Path(task_directory) / "scene-materials.json",
        "scene manifest",
    )
    script_manifest = require_task_artifact(
        task_directory,
        Path(task_directory) / "script.json",
        "script manifest",
    )

    scene_summary = validate_scene_manifest(
        document,
        _read_json_artifact(scene_manifest, "scene manifest"),
        task_directory,
        audio_file,
    )
    emphasis_summary = validate_emphasis_manifest(
        document,
        _read_json_artifact(emphasis_path, "emphasis manifest"),
    )
    validate_script_manifest(
        document,
        _read_json_artifact(script_manifest, "script manifest"),
    )
    media_summary = validate_media_outputs(
        audio_file,
        combined_video,
        final_video,
    )
    return AcceptanceArtifacts(
        final_video=final_video,
        combined_video=combined_video,
        emphasis_path=emphasis_path,
        scene_manifest=scene_manifest,
        script_manifest=script_manifest,
        audio_file=audio_file,
        scene_count=scene_summary.scene_count,
        material_count=scene_summary.material_count,
        cue_count=emphasis_summary.cue_count,
        audio_duration=media_summary.audio_duration,
        combined_duration=media_summary.combined_duration,
        final_duration=media_summary.final_duration,
        combined_narration_tail_brightness=(
            media_summary.combined_narration_tail_brightness
        ),
        combined_media_tail_brightness=(
            media_summary.combined_media_tail_brightness
        ),
        final_narration_tail_brightness=(
            media_summary.final_narration_tail_brightness
        ),
        final_media_tail_brightness=media_summary.final_media_tail_brightness,
    )


def preflight_environment() -> None:
    keys = config.app.get("pixabay_api_keys", [])
    if not isinstance(keys, list) or not any(str(key).strip() for key in keys):
        raise RuntimeError("Pixabay API key is not configured")

    settings = local_voice_service.settings_from_config(
        config.local_voice,
        PROJECT_ROOT,
    )
    if not settings.enabled:
        raise RuntimeError("local voice is not enabled")
    required_files = {
        "CosyVoice Python": settings.cosyvoice_python,
        "Qwen aligner Python": settings.aligner_python,
        "CosyVoice worker": settings.cosyvoice_worker,
        "Qwen aligner worker": settings.aligner_worker,
        "built-in voice reference": (
            settings.cosyvoice_repo / "asset" / "zero_shot_prompt.wav"
        ),
    }
    required_directories = {
        "CosyVoice model": settings.cosyvoice_model_dir,
        "Qwen aligner model": settings.aligner_model_dir,
        "CosyVoice repository": settings.cosyvoice_repo,
    }
    missing = [
        label
        for label, resource in required_files.items()
        if not Path(resource).is_file()
    ]
    missing.extend(
        label
        for label, resource in required_directories.items()
        if not Path(resource).is_dir()
    )
    if missing:
        raise RuntimeError("local voice resources are missing: " + ", ".join(missing))


@contextmanager
def forbid_llm_calls():
    names = (
        "generate_script",
        "generate_terms",
        "generate_emphasis_terms",
        "generate_social_metadata",
    )
    originals = {name: getattr(llm, name) for name in names}

    def forbidden(*args, **kwargs):
        raise RuntimeError("LLM calls are forbidden during Markdown acceptance")

    try:
        for name in names:
            setattr(llm, name, forbidden)
        yield
    finally:
        for name, original in originals.items():
            setattr(llm, name, original)


def validate_acceptance_document(document) -> None:
    emphasis_term_count = sum(
        len(row.emphasis_terms) for row in document.rows
    )
    if emphasis_term_count != EXPECTED_ACCEPTANCE_CUE_COUNT:
        raise ValueError(
            "acceptance Markdown must contain exactly "
            f"{EXPECTED_ACCEPTANCE_CUE_COUNT} emphasis terms, got "
            f"{emphasis_term_count}"
        )

    term_source_rows: dict[str, set[int]] = {}
    for row in document.rows:
        for term in row.emphasis_terms:
            term_source_rows.setdefault(term, set()).add(row.number)

    repeated_terms = {
        term: source_rows
        for term, source_rows in term_source_rows.items()
        if len(source_rows) >= 2
    }
    if not repeated_terms:
        raise ValueError(
            "acceptance Markdown must mark at least one emphasis term "
            "in two different source rows"
        )

    if not any(len(row.emphasis_terms) >= 2 for row in document.rows):
        raise ValueError(
            "acceptance Markdown must contain at least one row with "
            "two or more emphasis terms"
        )


def main() -> int:
    task_id = _task_id()
    document = load_acceptance_document()
    validate_acceptance_document(document)
    preflight_environment()
    task_directory = _reset_task_directory(task_id)

    # Acceptance generation must never publish externally, even when a local
    # config enables Upload-Post for normal WebUI tasks.
    upload_post.upload_post_service.auto_upload = False
    config.app["material_directory"] = "task"

    params = VideoParams(
        video_subject=document.title,
        video_script=document.script_text(),
        markdown_script=document,
        video_source="pixabay",
        video_aspect=VideoAspect.landscape,
        video_language="zh-CN",
        voice_name="local:default",
        subtitle_enabled=True,
        emphasis_enabled=True,
        emphasis_font_name="SimHei.ttf",
        emphasis_sfx_enabled=True,
        emphasis_sfx_volume=0.5,
        bgm_type="none",
        video_count=1,
    )

    with forbid_llm_calls():
        result = task.start(task_id, params)
    artifacts = validate_task_outputs(document, task_directory, result)

    print(f"task_id={task_id}")
    print(f"final={artifacts.final_video}")
    print(f"combined={artifacts.combined_video}")
    print(f"audio={artifacts.audio_file}")
    print(f"emphasis={artifacts.emphasis_path}")
    print(f"scene_manifest={artifacts.scene_manifest}")
    print(f"script_manifest={artifacts.script_manifest}")
    print(f"scenes={artifacts.scene_count}")
    print(f"materials={artifacts.material_count}")
    print(f"emphasis_cues={artifacts.cue_count}")
    print(f"audio_duration={artifacts.audio_duration:.3f}")
    print(f"combined_duration={artifacts.combined_duration:.3f}")
    print(f"final_duration={artifacts.final_duration:.3f}")
    print(
        "combined_narration_tail_brightness="
        f"{artifacts.combined_narration_tail_brightness:.3f}"
    )
    print(
        "combined_media_tail_brightness="
        f"{artifacts.combined_media_tail_brightness:.3f}"
    )
    print(
        "final_narration_tail_brightness="
        f"{artifacts.final_narration_tail_brightness:.3f}"
    )
    print(
        "final_media_tail_brightness="
        f"{artifacts.final_media_tail_brightness:.3f}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            "acceptance_error="
            f"{type(exc).__name__}: {_redact_configured_secrets(exc)}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
