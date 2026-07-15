from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from app.services import script_document
from scripts import generate_markdown_marriage_video as runner


def _document():
    return script_document.parse_markdown_script(
        runner.MARKDOWN_FILE.read_text(encoding="utf-8-sig")
    )


def _write(path: Path, content: bytes = b"artifact") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _scene_manifest(task_directory: Path, document, total_duration: float):
    scenes = script_document.build_scenes(document)
    boundaries = [0.0, 2.0, 4.0, total_duration]
    manifest = []
    for offset, scene in enumerate(scenes):
        material = _write(task_directory / f"material-{scene.index}.mp4")
        start = boundaries[offset]
        end = boundaries[offset + 1]
        manifest.append(
            {
                "scene_index": scene.index,
                "first_row": scene.first_row,
                "last_row": scene.last_row,
                "start": start,
                "end": end,
                "required_duration": end - start,
                "search_terms": list(scene.search_terms),
                "video_paths": [str(material)],
            }
        )
    return manifest


def _emphasis_manifest(document):
    return [
        {
            "text": term,
            "source_row_number": row.number,
            "sound_id": f"sound-{row.number}",
        }
        for row in document.rows
        for term in row.emphasis_terms
    ]


def _document_with_thirteen_terms():
    markdown = runner.MARKDOWN_FILE.read_text(encoding="utf-8-sig").replace(
        "婚姻；承诺",
        "婚姻；承诺；一句",
        1,
    )
    return script_document.parse_markdown_script(markdown)


class _FakeAudioClip:
    def __init__(self, duration: float, *, readable: bool = True):
        self.duration = duration
        self.readable = readable
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def get_frame(self, time):
        if not self.readable:
            raise OSError("unreadable audio stream")
        return np.zeros(2, dtype=float)

    def close(self):
        self.closed = True


class _FakeVideoClip:
    def __init__(
        self,
        duration: float,
        brightness: float,
        *,
        audio: _FakeAudioClip | None = None,
        readable: bool = True,
    ):
        self.duration = duration
        self.brightness = brightness
        self.audio = audio
        self.readable = readable
        self.closed = False
        self.frame_times = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def get_frame(self, time):
        if not self.readable:
            raise OSError("unreadable video stream")
        self.frame_times.append(time)
        brightness = (
            self.brightness(time)
            if callable(self.brightness)
            else self.brightness
        )
        return np.full((2, 2, 3), brightness, dtype=float)

    def close(self):
        self.closed = True
        if self.audio is not None:
            self.audio.close()


def _probe_media(
    *,
    audio_duration=10.0,
    combined_duration=10.0,
    final_duration=10.0,
    combined_brightness=80.0,
    final_brightness=70.0,
    final_audio=True,
    final_audio_readable=True,
    combined_readable=True,
):
    narration = _FakeAudioClip(audio_duration)
    combined = _FakeVideoClip(
        combined_duration,
        combined_brightness,
        readable=combined_readable,
    )
    final_audio_clip = (
        _FakeAudioClip(audio_duration, readable=final_audio_readable)
        if final_audio
        else None
    )
    final = _FakeVideoClip(
        final_duration,
        final_brightness,
        audio=final_audio_clip,
    )

    def open_video(path):
        return combined if Path(path).name == "combined.mp4" else final

    patches = (
        patch.object(runner, "AudioFileClip", return_value=narration),
        patch.object(runner, "VideoFileClip", side_effect=open_video),
    )
    return narration, combined, final, patches


def test_require_task_artifact_accepts_only_nonempty_files_inside_task_directory(
    tmp_path,
):
    task_directory = tmp_path / "task"
    accepted = _write(task_directory / "nested" / "final.mp4")

    assert (
        runner.require_task_artifact(task_directory, accepted, "final video")
        == accepted.resolve()
    )

    sibling = _write(tmp_path / "other-task" / "final.mp4")
    with pytest.raises(RuntimeError, match="outside current task directory"):
        runner.require_task_artifact(task_directory, sibling, "final video")

    empty = task_directory / "empty.json"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError, match="empty"):
        runner.require_task_artifact(task_directory, empty, "manifest")


def test_validate_scene_manifest_matches_document_timeline_and_materials(tmp_path):
    document = _document()
    task_directory = tmp_path / "task"
    audio_file = _write(task_directory / "audio.wav")
    expected_end = runner.video.get_required_video_duration(6.0)
    manifest = _scene_manifest(task_directory, document, expected_end)

    with patch.object(runner.voice, "get_audio_duration", return_value=6.0):
        summary = runner.validate_scene_manifest(
            document, manifest, task_directory, audio_file
        )

    assert summary.scene_count == 3
    assert summary.last_end == pytest.approx(expected_end)
    assert summary.material_count == 3


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest, task: manifest[0].update(start=0.1), "start at 0"),
        (lambda manifest, task: manifest[1].update(start=2.1), "continuous"),
        (
            lambda manifest, task: manifest[1].update(required_duration=1.0),
            "required_duration",
        ),
        (lambda manifest, task: manifest[1].update(first_row=2), "row range"),
        (
            lambda manifest, task: manifest[0].update(
                video_paths=[str(_write(task.parent / "old" / "material.mp4"))]
            ),
            "outside current task directory",
        ),
    ],
)
def test_validate_scene_manifest_rejects_inaccurate_or_external_data(
    tmp_path, mutation, message
):
    document = _document()
    task_directory = tmp_path / "task"
    audio_file = _write(task_directory / "audio.wav")
    expected_end = runner.video.get_required_video_duration(6.0)
    manifest = _scene_manifest(task_directory, document, expected_end)
    mutation(manifest, task_directory)

    with (
        patch.object(runner.voice, "get_audio_duration", return_value=6.0),
        pytest.raises(RuntimeError, match=message),
    ):
        runner.validate_scene_manifest(document, manifest, task_directory, audio_file)


def test_validate_emphasis_manifest_requires_all_row_bound_terms_and_sounds():
    document = _document()
    cues = _emphasis_manifest(document)

    summary = runner.validate_emphasis_manifest(document, cues)

    assert summary.cue_count == 12
    assert summary.repeated_term_rows == {"彼此": [4, 6]}
    assert summary.multi_cue_rows


def test_acceptance_document_rejects_thirteen_emphasis_terms():
    with pytest.raises(ValueError, match="exactly 12"):
        runner.validate_acceptance_document(_document_with_thirteen_terms())


def test_validate_emphasis_manifest_rejects_thirteen_cues_even_when_document_has_thirteen(
):
    document = _document_with_thirteen_terms()
    cues = _emphasis_manifest(document)

    assert len(cues) == 13
    with pytest.raises(RuntimeError, match="exactly 12"):
        runner.validate_emphasis_manifest(document, cues)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda cues: cues.pop(), "12"),
        (lambda cues: cues[0].update(source_row_number=2), "row-bound"),
        (lambda cues: cues[0].update(sound_id=""), "sound_id"),
    ],
)
def test_validate_emphasis_manifest_rejects_incomplete_cues(mutation, message):
    document = _document()
    cues = _emphasis_manifest(document)
    mutation(cues)

    with pytest.raises(RuntimeError, match=message):
        runner.validate_emphasis_manifest(document, cues)


def test_validate_script_manifest_requires_exact_markdown_document():
    document = _document()
    payload = {"params": {"markdown_script": document.model_dump(mode="json")}}

    runner.validate_script_manifest(document, payload)

    payload["params"]["markdown_script"]["rows"][0]["text"] = "旧任务文案"
    with pytest.raises(RuntimeError, match="does not match"):
        runner.validate_script_manifest(document, payload)


def test_validate_media_outputs_returns_actual_duration_and_tail_summary():
    narration, combined, final, patches = _probe_media()

    with patches[0], patches[1]:
        summary = runner.validate_media_outputs(
            "audio.wav",
            "combined.mp4",
            "final.mp4",
        )

    assert summary.audio_duration == pytest.approx(10.0)
    assert summary.combined_duration == pytest.approx(10.0)
    assert summary.final_duration == pytest.approx(10.0)
    assert summary.combined_narration_tail_brightness == pytest.approx(80.0)
    assert summary.combined_media_tail_brightness == pytest.approx(80.0)
    assert summary.final_narration_tail_brightness == pytest.approx(70.0)
    assert summary.final_media_tail_brightness == pytest.approx(70.0)
    assert narration.closed
    assert combined.closed
    assert final.closed


def test_validate_media_outputs_probes_both_narration_and_true_media_tails():
    _, combined, final, patches = _probe_media(
        audio_duration=10.0,
        combined_duration=20.0,
        final_duration=20.0,
    )

    with patches[0], patches[1]:
        runner.validate_media_outputs("audio.wav", "combined.mp4", "final.mp4")

    assert combined.frame_times == pytest.approx([9.9, 19.9])
    assert final.frame_times == pytest.approx([9.9, 19.9])


@pytest.mark.parametrize("black_media", ["combined", "final"])
def test_validate_media_outputs_rejects_black_true_tail_after_long_video_padding(
    black_media,
):
    bright_until_media_tail = lambda time: 80.0 if time < 19.0 else 0.0
    combined_brightness = (
        bright_until_media_tail if black_media == "combined" else 80.0
    )
    final_brightness = (
        bright_until_media_tail if black_media == "final" else 80.0
    )
    _, combined, final, patches = _probe_media(
        audio_duration=10.0,
        combined_duration=20.0,
        final_duration=20.0,
        combined_brightness=combined_brightness,
        final_brightness=final_brightness,
    )

    with (
        patches[0],
        patches[1],
        pytest.raises(
            RuntimeError,
            match=f"{black_media} video media-tail.*near-black",
        ),
    ):
        runner.validate_media_outputs("audio.wav", "combined.mp4", "final.mp4")

    target = combined if black_media == "combined" else final
    assert target.frame_times == pytest.approx([9.9, 19.9])


def test_validate_media_outputs_accepts_small_valid_media_duration_difference():
    _, combined, final, patches = _probe_media(
        audio_duration=10.0,
        combined_duration=10.05,
        final_duration=10.05,
    )

    with patches[0], patches[1]:
        runner.validate_media_outputs("audio.wav", "combined.mp4", "final.mp4")

    assert combined.frame_times == pytest.approx([9.9, 9.95])
    assert final.frame_times == pytest.approx([9.9, 9.95])


@pytest.mark.parametrize(
    ("combined_duration", "final_duration", "label"),
    [
        (9.97, 10.0, "combined video"),
        (10.0, 9.97, "final video"),
    ],
)
def test_validate_media_outputs_rejects_video_shorter_than_audio_beyond_tolerance(
    combined_duration, final_duration, label
):
    _, _, _, patches = _probe_media(
        combined_duration=combined_duration,
        final_duration=final_duration,
    )

    with (
        patches[0],
        patches[1],
        pytest.raises(RuntimeError, match=label + ".*shorter"),
    ):
        runner.validate_media_outputs("audio.wav", "combined.mp4", "final.mp4")


@pytest.mark.parametrize(
    ("combined_brightness", "final_brightness", "label"),
    [
        (18.0, 70.0, "combined video"),
        (80.0, 18.0, "final video"),
    ],
)
def test_validate_media_outputs_rejects_near_black_narration_tail(
    combined_brightness, final_brightness, label
):
    _, _, _, patches = _probe_media(
        combined_brightness=combined_brightness,
        final_brightness=final_brightness,
    )

    with (
        patches[0],
        patches[1],
        pytest.raises(RuntimeError, match=label + ".*near-black"),
    ):
        runner.validate_media_outputs("audio.wav", "combined.mp4", "final.mp4")


def test_validate_media_outputs_rejects_final_without_audio_track():
    _, _, _, patches = _probe_media(final_audio=False)

    with patches[0], patches[1], pytest.raises(RuntimeError, match="no audio stream"):
        runner.validate_media_outputs("audio.wav", "combined.mp4", "final.mp4")


def test_validate_media_outputs_rejects_unreadable_final_audio_stream():
    _, _, _, patches = _probe_media(final_audio_readable=False)

    with (
        patches[0],
        patches[1],
        pytest.raises(RuntimeError, match="audio stream.*unreadable"),
    ):
        runner.validate_media_outputs("audio.wav", "combined.mp4", "final.mp4")


def test_validate_media_outputs_rejects_unreadable_video_and_closes_open_resources():
    narration, combined, final, patches = _probe_media(combined_readable=False)

    with (
        patches[0],
        patches[1],
        pytest.raises(RuntimeError, match="unable to read media"),
    ):
        runner.validate_media_outputs("audio.wav", "combined.mp4", "final.mp4")

    assert narration.closed
    assert combined.closed
    assert final.closed


def test_validate_task_outputs_rejects_task_start_returning_external_old_video(
    tmp_path,
):
    document = _document()
    task_directory = tmp_path / "current-task"
    audio_file = _write(task_directory / "audio.wav")
    emphasis_file = _write(
        task_directory / "emphasis.json",
        json.dumps(_emphasis_manifest(document), ensure_ascii=False).encode("utf-8"),
    )
    _write(
        task_directory / "scene-materials.json",
        json.dumps(
            _scene_manifest(
                task_directory,
                document,
                runner.video.get_required_video_duration(6.0),
            )
        ).encode("utf-8"),
    )
    _write(
        task_directory / "script.json",
        json.dumps(
            {"params": {"markdown_script": document.model_dump(mode="json")}},
            ensure_ascii=False,
        ).encode("utf-8"),
    )
    combined = _write(task_directory / "combined-1.mp4")
    old_video = _write(tmp_path / "old-task" / "final-1.mp4")
    result = {
        "videos": [str(old_video)],
        "combined_videos": [str(combined)],
        "emphasis_path": str(emphasis_file),
        "audio_file": str(audio_file),
    }

    with (
        patch.object(runner.voice, "get_audio_duration", return_value=6.0),
        pytest.raises(RuntimeError, match="outside current task directory"),
    ):
        runner.validate_task_outputs(document, task_directory, result)


def test_validate_task_outputs_includes_direct_media_probe_summary(tmp_path):
    document = _document()
    task_directory = tmp_path / "current-task"
    final = _write(task_directory / "final-1.mp4")
    combined = _write(task_directory / "combined-1.mp4")
    audio = _write(task_directory / "audio.wav")
    emphasis = _write(task_directory / "emphasis.json", b"[]")
    _write(task_directory / "scene-materials.json", b"[]")
    _write(task_directory / "script.json", b"{}")
    result = {
        "videos": [str(final)],
        "combined_videos": [str(combined)],
        "emphasis_path": str(emphasis),
        "audio_file": str(audio),
    }
    media_summary = SimpleNamespace(
        audio_duration=21.85,
        combined_duration=21.90,
        final_duration=21.90,
        combined_narration_tail_brightness=101.0,
        combined_media_tail_brightness=100.0,
        final_narration_tail_brightness=99.0,
        final_media_tail_brightness=98.0,
    )

    with (
        patch.object(
            runner,
            "validate_scene_manifest",
            return_value=SimpleNamespace(scene_count=3, material_count=6),
        ),
        patch.object(
            runner,
            "validate_emphasis_manifest",
            return_value=SimpleNamespace(cue_count=12),
        ),
        patch.object(runner, "validate_script_manifest"),
        patch.object(
            runner,
            "validate_media_outputs",
            return_value=media_summary,
        ) as validate_media,
    ):
        artifacts = runner.validate_task_outputs(document, task_directory, result)

    validate_media.assert_called_once_with(
        audio.resolve(),
        combined.resolve(),
        final.resolve(),
    )
    assert artifacts.audio_duration == pytest.approx(21.85)
    assert artifacts.combined_duration == pytest.approx(21.90)
    assert artifacts.final_duration == pytest.approx(21.90)
    assert artifacts.combined_narration_tail_brightness == pytest.approx(101.0)
    assert artifacts.combined_media_tail_brightness == pytest.approx(100.0)
    assert artifacts.final_narration_tail_brightness == pytest.approx(99.0)
    assert artifacts.final_media_tail_brightness == pytest.approx(98.0)


def test_preflight_environment_checks_key_and_local_voice_resources(tmp_path):
    file_names = (
        "cosyvoice_python",
        "aligner_python",
        "cosyvoice_worker",
        "aligner_worker",
    )
    dir_names = (
        "cosyvoice_model_dir",
        "aligner_model_dir",
        "cosyvoice_repo",
    )
    values = {name: _write(tmp_path / name) for name in file_names}
    values.update(
        {
            name: (tmp_path / name)
            for name in dir_names
        }
    )
    for name in dir_names:
        values[name].mkdir()
    _write(values["cosyvoice_repo"] / "asset" / "zero_shot_prompt.wav")
    settings = SimpleNamespace(enabled=True, **values)

    with (
        patch.dict(runner.config.app, {"pixabay_api_keys": ["configured"]}),
        patch.object(
            runner.local_voice_service,
            "settings_from_config",
            return_value=settings,
        ),
    ):
        runner.preflight_environment()

    with (
        patch.dict(runner.config.app, {"pixabay_api_keys": []}),
        pytest.raises(RuntimeError, match="Pixabay API key"),
    ):
        runner.preflight_environment()


def test_main_preflights_before_reset_and_strictly_validates_after_task_start(
    tmp_path,
):
    document = _document()
    task_directory = tmp_path / "task"
    calls = []
    summary = SimpleNamespace(
        final_video=task_directory / "final.mp4",
        combined_video=task_directory / "combined.mp4",
        emphasis_path=task_directory / "emphasis.json",
        scene_manifest=task_directory / "scene-materials.json",
        script_manifest=task_directory / "script.json",
        audio_file=task_directory / "audio.wav",
        scene_count=3,
        material_count=3,
        cue_count=12,
        audio_duration=21.85,
        combined_duration=21.90,
        final_duration=21.90,
        combined_narration_tail_brightness=101.0,
        combined_media_tail_brightness=100.0,
        final_narration_tail_brightness=99.0,
        final_media_tail_brightness=98.0,
    )

    def record(name, result=None):
        def inner(*args, **kwargs):
            calls.append(name)
            return result

        return inner

    with (
        patch.dict(runner.config.app, {}, clear=False),
        patch.object(runner, "load_acceptance_document", return_value=document),
        patch.object(
            runner,
            "validate_acceptance_document",
            side_effect=record("document"),
        ),
        patch.object(
            runner, "preflight_environment", side_effect=record("preflight")
        ),
        patch.object(
            runner,
            "_reset_task_directory",
            side_effect=record("reset", task_directory),
        ),
        patch.object(
            runner.task, "start", side_effect=record("start", {"videos": ["x"]})
        ),
        patch.object(
            runner,
            "validate_task_outputs",
            side_effect=record("validate", summary),
        ),
    ):
        assert runner.main() == 0

    assert calls == ["document", "preflight", "reset", "start", "validate"]
    assert runner.upload_post.upload_post_service.auto_upload is False


def test_forbid_llm_calls_fails_closed():
    with runner.forbid_llm_calls():
        with pytest.raises(RuntimeError, match="forbidden"):
            runner.task.llm.generate_script(video_subject="x")
