import os
import shutil
import sys
import tempfile
import types
import unittest
import inspect
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from moviepy import (
    CompositeAudioClip,
    ImageClip,
    VideoFileClip,
)
import numpy as np
import pytest

# add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.models.schema import MaterialInfo, VideoAspect, VideoTransitionMode
from app.services.emphasis import EmphasisCue
from app.services import video as vd
from app.utils import utils

resources_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")


def test_required_video_duration_public_helper_preserves_legacy_margin():
    assert vd.get_required_video_duration(10.0) == pytest.approx(10.1)
    assert vd._get_required_video_duration(10.0) == pytest.approx(10.1)


def test_allocate_scene_clips_never_crosses_scene_duration():
    allocations = vd.allocate_scene_clips(
        video_paths=("a.mp4", "b.mp4"),
        source_durations={"a.mp4": 5.0, "b.mp4": 5.0},
        required_duration=6.5,
        max_clip_duration=5,
    )

    assert [(item.video_path, item.duration) for item in allocations] == [
        ("a.mp4", 5.0),
        ("b.mp4", 1.5),
    ]
    assert sum(item.duration for item in allocations) == 6.5


@pytest.mark.parametrize(
    ("required_duration", "max_clip_duration", "source_duration", "match"),
    [
        (0.0, 5.0, 5.0, "required duration"),
        (float("nan"), 5.0, 5.0, "required duration"),
        (float("inf"), 5.0, 5.0, "required duration"),
        (1.0, 0.0, 5.0, "maximum clip duration"),
        (1.0, float("nan"), 5.0, "maximum clip duration"),
        (1.0, float("inf"), 5.0, "maximum clip duration"),
        (1.0, 5.0, 0.0, "source duration"),
        (1.0, 5.0, float("nan"), "source duration"),
        (1.0, 5.0, float("inf"), "source duration"),
    ],
)
def test_allocate_scene_clips_rejects_invalid_durations(
    required_duration, max_clip_duration, source_duration, match
):
    with pytest.raises(ValueError, match=match):
        vd.allocate_scene_clips(
            video_paths=("a.mp4",),
            source_durations={"a.mp4": source_duration},
            required_duration=required_duration,
            max_clip_duration=max_clip_duration,
        )


def test_allocate_scene_clips_rejects_missing_source_duration():
    with pytest.raises(ValueError, match="missing source duration.*a.mp4"):
        vd.allocate_scene_clips(
            video_paths=("a.mp4",),
            source_durations={},
            required_duration=1.0,
            max_clip_duration=5,
        )


def test_allocate_scene_clips_rejects_insufficient_material():
    with pytest.raises(ValueError, match="scene material is 1.500s short"):
        vd.allocate_scene_clips(
            video_paths=("a.mp4",),
            source_durations={"a.mp4": 5.0},
            required_duration=6.5,
            max_clip_duration=5,
        )


def test_allocate_scene_clips_ignores_sub_millisecond_float_residue():
    allocations = vd.allocate_scene_clips(
        video_paths=("a.mp4", "next-scene.mp4"),
        source_durations={"a.mp4": 0.3, "next-scene.mp4": 5.0},
        required_duration=0.1 + 0.2,
        max_clip_duration=5,
    )

    assert [(item.video_path, item.duration) for item in allocations] == [
        ("a.mp4", 0.3)
    ]


def test_probe_video_duration_always_closes_clip():
    clip = types.SimpleNamespace(duration=4.25)
    with (
        patch.object(vd, "_open_video_clip_quietly", return_value=clip),
        patch.object(vd, "close_clip") as close,
    ):
        assert vd._probe_video_duration("a.mp4") == 4.25

    close.assert_called_once_with(clip)


def test_probe_video_duration_closes_clip_when_duration_is_invalid():
    clip = types.SimpleNamespace(duration="not-a-number")
    with (
        patch.object(vd, "_open_video_clip_quietly", return_value=clip),
        patch.object(vd, "close_clip") as close,
        pytest.raises(ValueError),
    ):
        vd._probe_video_duration("a.mp4")

    close.assert_called_once_with(clip)


def test_fit_scene_clip_returns_matching_size_without_changes():
    clip = types.SimpleNamespace(size=(1920, 1080))

    assert vd._fit_scene_clip(clip, 1920, 1080) is clip


def test_fit_scene_clip_resizes_matching_ratio():
    clip = MagicMock()
    clip.size = (1280, 720)
    clip.w = 1280
    clip.h = 720
    resized = object()
    clip.resized.return_value = resized

    assert vd._fit_scene_clip(clip, 1920, 1080) is resized
    clip.resized.assert_called_once_with(new_size=(1920, 1080))


def test_fit_scene_clip_letterboxes_mismatched_ratio_without_cropping():
    clip = MagicMock()
    clip.size = (640, 480)
    clip.w = 640
    clip.h = 480
    clip.duration = 2.0
    resized = MagicMock()
    resized.duration = 2.0
    positioned = object()
    resized.with_position.return_value = positioned
    clip.resized.return_value = resized
    background = MagicMock()
    background.with_duration.return_value = background
    composite = object()

    with (
        patch.object(vd, "ColorClip", return_value=background) as color_clip,
        patch.object(vd, "CompositeVideoClip", return_value=composite) as composite_clip,
    ):
        result = vd._fit_scene_clip(clip, 1920, 1080)

    assert result is composite
    clip.resized.assert_called_once_with(new_size=(1440, 1080))
    resized.with_position.assert_called_once_with("center")
    color_clip.assert_called_once_with(size=(1920, 1080), color=(0, 0, 0))
    background.with_duration.assert_called_once_with(2.0)
    composite_clip.assert_called_once_with([background, positioned])


def test_apply_scene_transition_accepts_none_and_rejects_unknown_value():
    clip = types.SimpleNamespace(duration=2.0)

    assert vd._apply_scene_transition(clip, VideoTransitionMode.none) is clip
    with pytest.raises(ValueError, match="unsupported video transition"):
        vd._apply_scene_transition(clip, "Spin")


def test_render_scene_allocation_honors_speed_and_exact_output_duration(tmp_path):
    class FakeClip:
        def __init__(self):
            self.duration = 5.0
            self.size = (1920, 1080)
            self.w = 1920
            self.h = 1080
            self.source_ranges = []

        def subclipped(self, start, end):
            self.source_ranges.append((start, end))
            self.duration = end - start
            return self

        def with_speed_scaled(self, speed):
            self.duration /= speed
            return self

    clip = FakeClip()
    allocation = vd.SceneClipAllocation("source.mp4", 2.0)
    written_durations = []

    def capture_write(value_clip, *_args, **_kwargs):
        written_durations.append(value_clip.duration)

    with (
        patch.object(vd, "_open_video_clip_quietly", return_value=clip),
        patch.object(vd, "_write_videofile_with_codec_fallback", side_effect=capture_write),
        patch.object(vd, "concat_video_clips_with_ffmpeg") as concat,
        patch.object(vd, "delete_files") as delete,
        patch.object(vd, "close_clip") as close,
    ):
        result = vd._render_scene_allocation(
            output_dir=str(tmp_path),
            scene_index=1,
            allocations=[allocation],
            video_aspect=VideoAspect.landscape,
            video_transition_mode=VideoTransitionMode.none,
            threads=2,
            clip_speed=2.0,
        )

    result_path = Path(result)
    assert result_path.parent == tmp_path
    assert result_path.name.startswith("scene-1-")
    assert result_path.suffix == ".mp4"
    assert clip.source_ranges == [(0, 4.0)]
    assert written_durations == [2.0]
    assert concat.call_args.kwargs["max_duration"] == 2.0
    clip_files = concat.call_args.kwargs["clip_files"]
    assert len(clip_files) == 1
    assert Path(clip_files[0]).parent == tmp_path
    assert Path(clip_files[0]).name.startswith("scene-1-clip-1-")
    close.assert_called_once_with(clip)
    delete.assert_called_once_with(clip_files)


def test_render_scene_allocation_normalizes_sub_millisecond_duration_residue(
    tmp_path,
):
    class FakeClip:
        def __init__(self):
            self.duration = 1.9995
            self.size = (1920, 1080)
            self.w = 1920
            self.h = 1080

        def subclipped(self, _start, end):
            self.duration = min(self.duration, end)
            return self

        def with_duration(self, duration):
            self.duration = duration
            return self

    clip = FakeClip()
    written_durations = []

    with (
        patch.object(vd, "_open_video_clip_quietly", return_value=clip),
        patch.object(
            vd,
            "_write_videofile_with_codec_fallback",
            side_effect=lambda value_clip, *_args, **_kwargs: written_durations.append(
                value_clip.duration
            ),
        ),
        patch.object(vd, "concat_video_clips_with_ffmpeg"),
        patch.object(vd, "delete_files"),
        patch.object(vd, "close_clip"),
    ):
        vd._render_scene_allocation(
            output_dir=str(tmp_path),
            scene_index=1,
            allocations=[vd.SceneClipAllocation("source.mp4", 2.0)],
            video_aspect=VideoAspect.landscape,
            video_transition_mode=VideoTransitionMode.none,
            threads=2,
            clip_speed=1.0,
        )

    assert written_durations == [2.0]


def test_render_scene_allocation_closes_clip_on_write_failure_without_deleting_source(
    tmp_path,
):
    clip = MagicMock()
    clip.duration = 2.0
    clip.size = (1920, 1080)
    clip.w = 1920
    clip.h = 1080
    clip.subclipped.return_value = clip
    allocation = vd.SceneClipAllocation("source.mp4", 2.0)

    with (
        patch.object(vd, "_open_video_clip_quietly", return_value=clip),
        patch.object(
            vd, "_write_videofile_with_codec_fallback", side_effect=RuntimeError("write failed")
        ),
        patch.object(vd, "delete_files") as delete,
        patch.object(vd, "close_clip") as close,
        pytest.raises(RuntimeError, match="write failed"),
    ):
        vd._render_scene_allocation(
            output_dir=str(tmp_path),
            scene_index=1,
            allocations=[allocation],
            video_aspect=VideoAspect.landscape,
            video_transition_mode=VideoTransitionMode.none,
            threads=2,
            clip_speed=1.0,
        )

    close.assert_called_once_with(clip)
    assert all("source.mp4" not in str(call) for call in delete.call_args_list)


@pytest.mark.parametrize("write_fails", [False, True])
def test_render_scene_allocation_never_overwrites_or_deletes_same_named_source(
    tmp_path, write_fails
):
    class FakeClip:
        duration = 2.0
        size = (1920, 1080)
        w = 1920
        h = 1080

        def subclipped(self, _start, _end):
            return self

    source_path = tmp_path / "scene-1-clip-1.mp4"
    original_content = b"original-source-video"
    source_path.write_bytes(original_content)

    def write_clip(_clip, output_file, **_kwargs):
        Path(output_file).write_bytes(b"temporary-render")
        if write_fails:
            raise RuntimeError("write failed")

    def concat_scene(*, output_file, **_kwargs):
        Path(output_file).write_bytes(b"scene-render")

    context = (
        pytest.raises(RuntimeError, match="write failed")
        if write_fails
        else nullcontext()
    )
    with (
        patch.object(vd, "_open_video_clip_quietly", return_value=FakeClip()),
        patch.object(
            vd, "_write_videofile_with_codec_fallback", side_effect=write_clip
        ),
        patch.object(vd, "concat_video_clips_with_ffmpeg", side_effect=concat_scene),
        patch.object(vd, "close_clip"),
        context,
    ):
        vd._render_scene_allocation(
            output_dir=str(tmp_path),
            scene_index=1,
            allocations=[vd.SceneClipAllocation(str(source_path), 2.0)],
            video_aspect=VideoAspect.landscape,
            video_transition_mode=VideoTransitionMode.none,
            threads=2,
            clip_speed=1.0,
        )

    assert source_path.exists()
    assert source_path.read_bytes() == original_content


def test_render_scene_allocation_cleans_incomplete_scene_when_concat_fails(tmp_path):
    class FakeClip:
        duration = 2.0
        size = (1920, 1080)
        w = 1920
        h = 1080

        def subclipped(self, _start, _end):
            return self

    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source-video")

    def write_clip(_clip, output_file, **_kwargs):
        Path(output_file).write_bytes(b"temporary-clip")

    def fail_scene_concat(*, output_file, **_kwargs):
        Path(output_file).write_bytes(b"incomplete-scene")
        raise RuntimeError("scene concat failed")

    with (
        patch.object(vd, "_open_video_clip_quietly", return_value=FakeClip()),
        patch.object(
            vd, "_write_videofile_with_codec_fallback", side_effect=write_clip
        ),
        patch.object(
            vd, "concat_video_clips_with_ffmpeg", side_effect=fail_scene_concat
        ),
        patch.object(vd, "close_clip"),
        pytest.raises(RuntimeError, match="scene concat failed"),
    ):
        vd._render_scene_allocation(
            output_dir=str(tmp_path),
            scene_index=1,
            allocations=[vd.SceneClipAllocation(str(source_path), 2.0)],
            video_aspect=VideoAspect.landscape,
            video_transition_mode=VideoTransitionMode.none,
            threads=2,
            clip_speed=1.0,
        )

    assert list(tmp_path.iterdir()) == [source_path]
    assert source_path.read_bytes() == b"source-video"


def test_concat_video_clips_cleans_unique_list_when_list_build_fails(tmp_path):
    clip_file = tmp_path / "source.mp4"
    clip_file.write_bytes(b"source-video")

    with (
        patch.object(
            vd,
            "_format_ffmpeg_concat_path",
            side_effect=RuntimeError("list build failed"),
        ),
        pytest.raises(RuntimeError, match="list build failed"),
    ):
        vd.concat_video_clips_with_ffmpeg(
            clip_files=[str(clip_file)],
            output_file=str(tmp_path / "combined.mp4"),
            threads=1,
            output_dir=str(tmp_path),
        )

    assert not list(tmp_path.glob("ffmpeg-concat-*.txt"))
    assert clip_file.read_bytes() == b"source-video"


def test_combine_scene_videos_concatenates_in_scene_order(tmp_path):
    plans = [
        types.SimpleNamespace(
            scene_index=2, required_duration=3.0, video_paths=("b.mp4",)
        ),
        types.SimpleNamespace(
            scene_index=1, required_duration=2.0, video_paths=("a.mp4",)
        ),
    ]
    rendered_scene_indexes = []

    def render_scene(**kwargs):
        rendered_scene_indexes.append(kwargs["scene_index"])
        scene_file = Path(kwargs["output_dir"]) / f"scene-{kwargs['scene_index']}.mp4"
        scene_file.write_bytes(b"scene")
        return str(scene_file)

    def concat_scenes(*, output_file, **_kwargs):
        Path(output_file).write_bytes(b"combined")

    combined_path = tmp_path / "combined.mp4"
    with (
        patch.object(
            vd,
            "_probe_video_duration",
            side_effect={"a.mp4": 4.0, "b.mp4": 4.0}.get,
        ),
        patch.object(
            vd,
            "_render_scene_allocation",
            side_effect=render_scene,
        ) as render,
        patch.object(
            vd, "concat_video_clips_with_ffmpeg", side_effect=concat_scenes
        ) as concat,
    ):
        result = vd.combine_scene_videos(
            str(combined_path),
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert result == str(combined_path)
    assert rendered_scene_indexes == [1, 2]
    assert [Path(path).name for path in concat.call_args.kwargs["clip_files"]] == [
        "scene-1.mp4",
        "scene-2.mp4",
    ]
    assert concat.call_args.kwargs["max_duration"] == 5.0
    work_directories = {Path(call.kwargs["output_dir"]) for call in render.call_args_list}
    assert len(work_directories) == 1
    assert not next(iter(work_directories)).exists()
    assert combined_path.read_bytes() == b"combined"


def test_combine_scene_videos_isolates_each_call_and_concat_list(tmp_path):
    plans = [
        types.SimpleNamespace(
            scene_index=1, required_duration=1.0, video_paths=("source.mp4",)
        )
    ]
    render_directories = []
    ffmpeg_commands = []

    def render_scene(**kwargs):
        render_directory = Path(kwargs["output_dir"])
        render_directories.append(render_directory)
        scene_file = render_directory / "mock-scene.mp4"
        scene_file.write_bytes(b"scene")
        return str(scene_file)

    def run_ffmpeg(command, capture_output, text, check):
        ffmpeg_commands.append(command)
        Path(command[-1]).write_bytes(b"combined")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch.object(vd, "_probe_video_duration", return_value=2.0),
        patch.object(vd, "_render_scene_allocation", side_effect=render_scene),
        patch.object(vd, "_get_effective_video_codec", return_value="libx264"),
        patch.object(vd.subprocess, "run", side_effect=run_ffmpeg),
    ):
        first_output = tmp_path / "first.mp4"
        second_output = tmp_path / "second.mp4"
        vd.combine_scene_videos(
            str(first_output),
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )
        vd.combine_scene_videos(
            str(second_output),
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert render_directories[0] != render_directories[1]
    concat_lists = [
        Path(command[command.index("-i") + 1]) for command in ffmpeg_commands
    ]
    assert len(set(concat_lists)) == 2
    assert concat_lists[0].parent == render_directories[0]
    assert concat_lists[1].parent == render_directories[1]
    assert first_output.read_bytes() == b"combined"
    assert second_output.read_bytes() == b"combined"
    assert all(not directory.exists() for directory in render_directories)


def test_combine_scene_videos_cleans_call_temp_tree_when_later_scene_render_fails(
    tmp_path,
):
    source_a = tmp_path / "source-a.mp4"
    source_b = tmp_path / "source-b.mp4"
    source_a.write_bytes(b"source-a")
    source_b.write_bytes(b"source-b")
    existing_output = tmp_path / "combined.mp4"
    existing_output.write_bytes(b"existing-output")
    plans = [
        types.SimpleNamespace(
            scene_index=1, required_duration=1.0, video_paths=(str(source_a),)
        ),
        types.SimpleNamespace(
            scene_index=2, required_duration=1.0, video_paths=(str(source_b),)
        ),
    ]
    render_directories = []

    def render_scene(**kwargs):
        work_dir = Path(kwargs["output_dir"])
        render_directories.append(work_dir)
        (work_dir / f"scene-{kwargs['scene_index']}-clip-leftover.mp4").write_bytes(
            b"clip"
        )
        (work_dir / "ffmpeg-concat-leftover.txt").write_text(
            "temporary list", encoding="utf-8"
        )
        scene_file = work_dir / f"scene-{kwargs['scene_index']}.mp4"
        scene_file.write_bytes(b"partial-scene")
        if kwargs["scene_index"] == 2:
            raise RuntimeError("second scene failed")
        return str(scene_file)

    with (
        patch.object(vd, "_probe_video_duration", return_value=2.0),
        patch.object(vd, "_render_scene_allocation", side_effect=render_scene),
        pytest.raises(RuntimeError, match="second scene failed"),
    ):
        vd.combine_scene_videos(
            str(existing_output),
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert len(set(render_directories)) == 1
    assert not render_directories[0].exists()
    assert existing_output.read_bytes() == b"existing-output"
    assert source_a.read_bytes() == b"source-a"
    assert source_b.read_bytes() == b"source-b"


def test_combine_scene_videos_preserves_existing_output_when_final_concat_fails(
    tmp_path,
):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source-video")
    existing_output = tmp_path / "combined.mp4"
    existing_output.write_bytes(b"existing-output")
    plan = types.SimpleNamespace(
        scene_index=1,
        required_duration=1.0,
        video_paths=(str(source_path),),
    )
    render_directories = []

    def render_scene(**kwargs):
        work_dir = Path(kwargs["output_dir"])
        render_directories.append(work_dir)
        scene_file = work_dir / "scene-1.mp4"
        scene_file.write_bytes(b"scene")
        return str(scene_file)

    def fail_concat(*, output_file, output_dir, **_kwargs):
        Path(output_file).write_bytes(b"incomplete-output")
        Path(output_dir, "ffmpeg-concat-leftover.txt").write_text(
            "temporary list", encoding="utf-8"
        )
        raise RuntimeError("final concat failed")

    with (
        patch.object(vd, "_probe_video_duration", return_value=2.0),
        patch.object(vd, "_render_scene_allocation", side_effect=render_scene),
        patch.object(vd, "concat_video_clips_with_ffmpeg", side_effect=fail_concat),
        pytest.raises(RuntimeError, match="final concat failed"),
    ):
        vd.combine_scene_videos(
            str(existing_output),
            [plan],
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert not render_directories[0].exists()
    assert existing_output.read_bytes() == b"existing-output"
    assert source_path.read_bytes() == b"source-video"


def test_combine_scene_videos_rejects_final_output_that_is_a_source(tmp_path):
    source_and_output = tmp_path / "source.mp4"
    source_and_output.write_bytes(b"source-video")
    plan = types.SimpleNamespace(
        scene_index=1,
        required_duration=1.0,
        video_paths=(str(source_and_output),),
    )

    with (
        patch.object(vd, "_probe_video_duration", return_value=2.0),
        patch.object(vd, "_render_scene_allocation", return_value="scene.mp4"),
        patch.object(vd, "concat_video_clips_with_ffmpeg"),
        pytest.raises(ValueError, match="must not overwrite a source video"),
    ):
        vd.combine_scene_videos(
            str(source_and_output),
            [plan],
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert source_and_output.read_bytes() == b"source-video"


def _make_scene_cleanup_lock_case(tmp_path, concat_error=None):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source-video")
    output_path = tmp_path / "combined.mp4"
    if concat_error is not None:
        output_path.write_bytes(b"existing-output")
    plan = types.SimpleNamespace(
        scene_index=1,
        required_duration=1.0,
        video_paths=(str(source_path),),
    )
    work_directories = []

    def render_scene(**kwargs):
        work_dir = Path(kwargs["output_dir"])
        work_directories.append(work_dir)
        scene_file = work_dir / "scene-1.mp4"
        scene_file.write_bytes(b"scene")
        return str(scene_file)

    def concat_scene(*, output_file, **_kwargs):
        Path(output_file).write_bytes(b"combined-output")
        if concat_error is not None:
            raise concat_error

    return plan, source_path, output_path, work_directories, render_scene, concat_scene


def test_combine_scene_videos_returns_success_when_cleanup_stays_locked(tmp_path):
    (
        plan,
        source_path,
        output_path,
        work_directories,
        render_scene,
        concat_scene,
    ) = _make_scene_cleanup_lock_case(tmp_path)
    lock_error = PermissionError("sensitive lock details")

    with (
        patch.object(vd, "_probe_video_duration", return_value=2.0),
        patch.object(vd, "_render_scene_allocation", side_effect=render_scene),
        patch.object(
            vd, "concat_video_clips_with_ffmpeg", side_effect=concat_scene
        ),
        patch.object(vd.shutil, "rmtree", side_effect=lock_error) as rmtree,
        patch.object(vd.logger, "warning") as warning,
        patch.object(vd.time, "sleep") as sleep,
    ):
        result = vd.combine_scene_videos(
            str(output_path),
            [plan],
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert result == str(output_path)
    assert output_path.read_bytes() == b"combined-output"
    assert source_path.read_bytes() == b"source-video"
    assert rmtree.call_count == 3
    assert sleep.call_count == 2
    warning.assert_called_once()
    warning_text = " ".join(str(value) for value in warning.call_args.args)
    assert str(work_directories[0]) in warning_text
    assert "PermissionError" in warning_text
    assert "sensitive lock details" not in warning_text


def test_combine_scene_videos_preserves_business_error_when_cleanup_is_locked(
    tmp_path,
):
    original_error = RuntimeError("original ffmpeg failure")
    (
        plan,
        source_path,
        output_path,
        work_directories,
        render_scene,
        concat_scene,
    ) = _make_scene_cleanup_lock_case(tmp_path, concat_error=original_error)

    with (
        patch.object(vd, "_probe_video_duration", return_value=2.0),
        patch.object(vd, "_render_scene_allocation", side_effect=render_scene),
        patch.object(
            vd, "concat_video_clips_with_ffmpeg", side_effect=concat_scene
        ),
        patch.object(
            vd.shutil,
            "rmtree",
            side_effect=PermissionError("sensitive cleanup failure"),
        ) as rmtree,
        patch.object(vd.logger, "warning") as warning,
        patch.object(vd.time, "sleep") as sleep,
        pytest.raises(RuntimeError) as raised,
    ):
        vd.combine_scene_videos(
            str(output_path),
            [plan],
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert raised.value is original_error
    assert output_path.read_bytes() == b"existing-output"
    assert source_path.read_bytes() == b"source-video"
    assert rmtree.call_count == 3
    assert sleep.call_count == 2
    warning.assert_called_once()
    warning_text = " ".join(str(value) for value in warning.call_args.args)
    assert str(work_directories[0]) in warning_text
    assert "PermissionError" in warning_text
    assert "sensitive cleanup failure" not in warning_text


def test_combine_scene_videos_retries_transient_cleanup_lock(tmp_path):
    (
        plan,
        source_path,
        output_path,
        _work_directories,
        render_scene,
        concat_scene,
    ) = _make_scene_cleanup_lock_case(tmp_path)

    with (
        patch.object(vd, "_probe_video_duration", return_value=2.0),
        patch.object(vd, "_render_scene_allocation", side_effect=render_scene),
        patch.object(
            vd, "concat_video_clips_with_ffmpeg", side_effect=concat_scene
        ),
        patch.object(
            vd.shutil,
            "rmtree",
            side_effect=[PermissionError("locked"), OSError("busy"), None],
        ) as rmtree,
        patch.object(vd.logger, "warning") as warning,
        patch.object(vd.time, "sleep") as sleep,
    ):
        result = vd.combine_scene_videos(
            str(output_path),
            [plan],
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )

    assert result == str(output_path)
    assert output_path.read_bytes() == b"combined-output"
    assert source_path.read_bytes() == b"source-video"
    assert rmtree.call_count == 3
    assert sleep.call_count == 2
    warning.assert_not_called()


def test_cleanup_scene_work_dir_treats_missing_directory_as_clean():
    with (
        patch.object(
            vd.shutil, "rmtree", side_effect=FileNotFoundError("already gone")
        ) as rmtree,
        patch.object(vd.time, "sleep") as sleep,
        patch.object(vd.logger, "warning") as warning,
    ):
        vd._cleanup_scene_work_dir("missing-scene-work-dir")

    rmtree.assert_called_once_with("missing-scene-work-dir")
    sleep.assert_not_called()
    warning.assert_not_called()


@pytest.mark.parametrize("plans", [[], ()])
def test_combine_scene_videos_rejects_empty_scene_plans(plans):
    with pytest.raises(ValueError, match="scene plans must not be empty"):
        vd.combine_scene_videos(
            "combined.mp4",
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )


def test_combine_scene_videos_rejects_duplicate_scene_indexes():
    plans = [
        types.SimpleNamespace(scene_index=1, required_duration=1.0, video_paths=("a",)),
        types.SimpleNamespace(scene_index=1, required_duration=1.0, video_paths=("b",)),
    ]
    with pytest.raises(ValueError, match="duplicate scene index: 1"):
        vd.combine_scene_videos(
            "combined.mp4",
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )


@pytest.mark.parametrize("required_duration", [0.0, float("nan"), float("inf")])
def test_combine_scene_videos_rejects_invalid_required_duration(required_duration):
    plans = [
        types.SimpleNamespace(
            scene_index=1,
            required_duration=required_duration,
            video_paths=("a.mp4",),
        )
    ]
    with pytest.raises(ValueError, match="scene 1 required duration"):
        vd.combine_scene_videos(
            "combined.mp4",
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )


@pytest.mark.parametrize("probed_duration", [0.0, float("nan"), float("inf")])
def test_combine_scene_videos_rejects_invalid_probed_duration(probed_duration):
    plans = [
        types.SimpleNamespace(
            scene_index=1, required_duration=1.0, video_paths=("a.mp4",)
        )
    ]
    with (
        patch.object(vd, "_probe_video_duration", return_value=probed_duration),
        pytest.raises(ValueError, match="source duration.*a.mp4"),
    ):
        vd.combine_scene_videos(
            "combined.mp4",
            plans,
            VideoAspect.landscape,
            VideoTransitionMode.none,
            5,
            2,
            1.0,
        )


class TestVideoService(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)
        self.test_img_path = os.path.join(resources_dir, "1.png")
        vd._runtime_disabled_video_codecs.clear()
        vd._ffmpeg_encoder_exists.cache_clear()

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)
        vd._runtime_disabled_video_codecs.clear()
        vd._ffmpeg_encoder_exists.cache_clear()

    def test_create_emphasis_text_clips_stays_above_bottom_subtitles(self):
        cue = EmphasisCue(
            text="不是年轻人",
            start=1.0,
            end=2.0,
            color="#FF5A36",
            animation="pop",
            sound_id="pop-01",
            position="left",
        )
        font_path = str(Path(utils.font_dir()) / "STHeitiMedium.ttc")

        clips = vd.create_emphasis_text_clips(
            cues=[cue],
            video_size=(1920, 1080),
            font_path=font_path,
            font_size=92,
        )
        try:
            self.assertEqual(len(clips), 1)
            self.assertEqual(clips[0].start, 1.0)
            self.assertEqual(clips[0].end, 2.0)
            self.assertLess(clips[0].pos(0)[1], 1080 * 0.8)
        finally:
            for clip in clips:
                clip.close()

    def test_resolve_emphasis_font_prefers_requested_project_font(self):
        path = vd.resolve_emphasis_font_path("SimHei.ttf", "婚姻二字")

        self.assertEqual(Path(path).name, "SimHei.ttf")

    def test_three_emphasis_layers_are_ordered_and_center_is_supported(self):
        cues = [
            EmphasisCue(
                "第一重点", 0, 2, "#FF5A36", "pop", "pop-01", "left", 1, 0
            ),
            EmphasisCue(
                "第二重点", 0.5, 2, "#FFB000", "zoom", "hit-01", "center", 1, 1
            ),
            EmphasisCue(
                "第三重点", 1, 2, "#A86BFF", "shake", "pop-02", "right", 1, 2
            ),
        ]
        font_path = vd.resolve_emphasis_font_path(
            "SimHei.ttf", "第一重点第二重点第三重点"
        )

        clips = vd.create_emphasis_text_clips(
            cues,
            video_size=(1920, 1080),
            font_path=font_path,
            font_size=92,
        )
        try:
            positions = [clip.pos(0.3) for clip in clips]
            self.assertLess(positions[0][1], positions[1][1])
            self.assertLess(positions[1][1], positions[2][1])
            rendered_width = clips[1].get_frame(0.3).shape[1]
            center_x = positions[1][0] + rendered_width / 2
            self.assertAlmostEqual(center_x, 960, delta=60)
            self.assertLess(positions[2][1] + clips[2].h, 1080 * 0.72)
        finally:
            for clip in clips:
                clip.close()

    def test_create_emphasis_slide_animation_stays_inside_horizontal_safe_area(self):
        cue = EmphasisCue(
            text="不是找一个完美的人",
            start=1.0,
            end=2.0,
            color="#FF5A36",
            animation="slide_right",
            sound_id="whoosh-01",
            position="right",
        )
        video_width = 1920
        safe_margin = 40
        font_path = str(Path(utils.font_dir()) / "STHeitiMedium.ttc")

        clips = vd.create_emphasis_text_clips(
            cues=[cue],
            video_size=(video_width, 1080),
            font_path=font_path,
            font_size=92,
        )
        try:
            for current_time in (0, 0.11, 0.22):
                x, _ = clips[0].pos(current_time)
                self.assertGreaterEqual(x, safe_margin)
                self.assertLessEqual(x + clips[0].w, video_width - safe_margin)
        finally:
            for clip in clips:
                clip.close()

    def test_create_emphasis_text_clips_adds_vertical_padding_for_font_bottom(self):
        cue = EmphasisCue(
            text="不是找一个完美的人",
            start=1.0,
            end=2.0,
            color="#FF5A36",
            animation="slide_left",
            sound_id="whoosh-01",
            position="left",
        )
        font_path = str(Path(utils.font_dir()) / "MicrosoftYaHeiBold.ttc")

        clips = vd.create_emphasis_text_clips(
            cues=[cue],
            video_size=(1920, 1080),
            font_path=font_path,
            font_size=92,
        )
        try:
            mask = clips[0].mask.get_frame(0)
            visible_rows = np.where((mask > 0.01).any(axis=1))[0]

            self.assertGreater(visible_rows[0], 0)
            self.assertLess(visible_rows[-1], clips[0].h - 1)
        finally:
            for clip in clips:
                clip.close()

    def test_create_emphasis_scaled_animation_stays_inside_horizontal_safe_area(self):
        cue = EmphasisCue(
            text="不是找一个完美的人",
            start=1.0,
            end=2.0,
            color="#FF5A36",
            animation="pop",
            sound_id="pop-01",
            position="right",
        )
        video_width = 1920
        safe_margin = 40
        font_path = str(Path(utils.font_dir()) / "STHeitiMedium.ttc")

        clips = vd.create_emphasis_text_clips(
            cues=[cue],
            video_size=(video_width, 1080),
            font_path=font_path,
            font_size=92,
        )
        try:
            current_time = 0.22
            x, _ = clips[0].pos(current_time)
            rendered_width = clips[0].get_frame(current_time).shape[1]
            self.assertGreaterEqual(x, safe_margin)
            self.assertLessEqual(x + rendered_width, video_width - safe_margin)
        finally:
            for clip in clips:
                clip.close()

    def test_create_emphasis_shake_animation_stays_inside_horizontal_safe_area(self):
        cue = EmphasisCue(
            text="这是一个特别特别特别特别重要的重点词",
            start=1.0,
            end=2.0,
            color="#FF5A36",
            animation="shake",
            sound_id="hit-01",
            position="left",
        )
        video_width = 1080
        safe_margin = 40
        font_path = str(Path(utils.font_dir()) / "STHeitiMedium.ttc")

        clips = vd.create_emphasis_text_clips(
            cues=[cue],
            video_size=(video_width, 1920),
            font_path=font_path,
            font_size=92,
        )
        try:
            current_time = 3 * np.pi / (2 * 55)
            x, _ = clips[0].pos(current_time)
            self.assertGreaterEqual(x, safe_margin)
            self.assertLessEqual(x + clips[0].w, video_width - safe_margin)
        finally:
            for clip in clips:
                clip.close()

    def test_create_emphasis_audio_clips_returns_one_clip_per_cue(self):
        cues = [
            EmphasisCue("不是年轻人", 1.0, 2.0, "#FF5A36", "pop", "pop-01", "left"),
            EmphasisCue("逃避吃苦", 2.0, 3.0, "#FFB000", "slide_left", "whoosh-01", "right"),
            EmphasisCue("一起面对", 3.0, 4.0, "#A86BFF", "zoom", "hit-01", "left"),
        ]
        project_root = Path(__file__).parent.parent.parent

        clips = vd.create_emphasis_audio_clips(
            cues,
            sfx_root=project_root / "resource" / "sfx",
            volume=0.16,
        )
        try:
            self.assertEqual(len(clips), 3)
            self.assertEqual([clip.start for clip in clips], [cue.start for cue in cues])
            # CompositeAudioClip 会把整批时间值传给每个子 clip；短音效在
            # 有效时长之外必须静音，不能让底层 FFmpeg reader 越界报错。
            composite = CompositeAudioClip(clips)
            try:
                frame = composite.get_frame(np.linspace(cues[0].start, cues[0].start + 1, 2000))
                self.assertEqual(frame.shape, (2000, 2))
                self.assertTrue(np.isfinite(frame).all())
            finally:
                composite.close()
        finally:
            for clip in clips:
                clip.close()

    def test_create_emphasis_audio_clips_normalizes_peak_before_mix(self):
        cue = EmphasisCue(
            "重点词", 1.0, 2.0, "#FF5A36", "slide_left", "whoosh-03", "left"
        )
        project_root = Path(__file__).parent.parent.parent

        clips = vd.create_emphasis_audio_clips(
            [cue],
            sfx_root=project_root / "resource" / "sfx",
            volume=0.35,
        )
        try:
            composite = CompositeAudioClip(clips)
            try:
                timeline = np.arange(0, clips[0].duration, 1 / clips[0].fps)
                frames = composite.get_frame(clips[0].start + timeline)
                self.assertAlmostEqual(float(np.abs(frames).max()), 0.85 * 0.35, delta=0.01)
            finally:
                composite.close()
        finally:
            for clip in clips:
                clip.close()

    def test_generate_video_accepts_optional_emphasis_manifest_path(self):
        parameter = inspect.signature(vd.generate_video).parameters["emphasis_path"]

        self.assertEqual(parameter.default, "")

    def test_preprocess_video(self):
        if not os.path.exists(self.test_img_path):
            self.fail(f"test image not found: {self.test_img_path}")

        local_videos_dir = utils.storage_dir("local_videos", create=True)
        safe_img_path = os.path.join(local_videos_dir, "test-preprocess-1.png")
        shutil.copy2(self.test_img_path, safe_img_path)

        # test preprocess_video function
        m = MaterialInfo()
        m.url = os.path.basename(safe_img_path)
        m.provider = "local"
        print(m)

        try:
            materials = vd.preprocess_video([m], clip_duration=4)
            print(materials)

            # verify result
            self.assertIsNotNone(materials)
            self.assertEqual(len(materials), 1)
            self.assertTrue(materials[0].url.endswith(".mp4"))

            # moviepy get video info
            clip = VideoFileClip(materials[0].url)
            try:
                print(clip)
            finally:
                clip.close()

            # clean generated test video file
            if os.path.exists(materials[0].url):
                os.remove(materials[0].url)
        finally:
            if os.path.exists(safe_img_path):
                os.remove(safe_img_path)

    def test_preprocess_video_rejects_material_outside_local_videos(self):
        """
        local 素材路径来自 API 参数，不能允许任意绝对路径进入 MoviePy。
        这里验证非 local_videos 白名单目录内的路径会被跳过，避免任意文件读取。
        """
        m = MaterialInfo(provider="local", url=self.test_img_path)

        materials = vd.preprocess_video([m], clip_duration=4)

        self.assertEqual(materials, [])

    def test_get_bgm_file_accepts_song_directory_filename(self):
        """
        BGM 列表接口现在只暴露文件名；生成视频时应能把文件名安全解析回
        resource/songs 白名单目录，保持正常使用路径可用。
        """
        song_dir = utils.song_dir()
        bgm_path = os.path.join(song_dir, "test-safe-bgm.mp3")
        Path(bgm_path).write_bytes(b"fake-mp3")

        try:
            self.assertEqual(vd.get_bgm_file(bgm_file="test-safe-bgm.mp3"), bgm_path)
        finally:
            if os.path.exists(bgm_path):
                os.remove(bgm_path)

    def test_get_bgm_file_accepts_project_relative_song_path(self):
        """
        用户在 WebUI 中可能直接填写 ./resource/songs/xxx.mp3。该路径虽然是
        项目根目录相对路径，但实际文件仍在 resource/songs 白名单目录内，
        应该被接受，避免自定义背景音乐被误判为不存在。
        """
        song_dir = utils.song_dir()
        bgm_path = os.path.join(song_dir, "test-relative-bgm.mp3")
        Path(bgm_path).write_bytes(b"fake-mp3")

        try:
            self.assertEqual(
                vd.get_bgm_file(bgm_file="./resource/songs/test-relative-bgm.mp3"),
                bgm_path,
            )
        finally:
            if os.path.exists(bgm_path):
                os.remove(bgm_path)

    def test_get_bgm_file_rejects_path_outside_song_directory(self):
        """
        用户传入的 bgm_file 不能直接作为本地路径打开，否则可能读取系统文件。
        即使外部文件存在，也必须因为不在 songs 目录内被拒绝。
        """
        with tempfile.NamedTemporaryFile(suffix=".mp3") as temp_bgm:
            self.assertEqual(vd.get_bgm_file(bgm_file=temp_bgm.name), "")

    def test_get_ffmpeg_binary_uses_configured_env_path(self):
        """配置中显式指定 ffmpeg 时，应优先使用该路径。"""
        with patch.dict(os.environ, {"IMAGEIO_FFMPEG_EXE": "/tmp/custom-ffmpeg"}, clear=True):
            self.assertEqual(utils.get_ffmpeg_binary(), "/tmp/custom-ffmpeg")

    def test_get_ffmpeg_binary_falls_back_to_imageio_ffmpeg(self):
        """
        Windows 便携包里系统 PATH 可能没有 ffmpeg，但 moviepy 依赖的
        imageio-ffmpeg 通常会提供可执行文件。这里验证该兜底路径可用。
        """
        fake_imageio_ffmpeg = types.SimpleNamespace(
            get_ffmpeg_exe=lambda: "/tmp/bundled-ffmpeg"
        )

        with patch.dict(os.environ, {}, clear=True), patch.object(
            utils.shutil, "which", return_value=None
        ), patch.dict(sys.modules, {"imageio_ffmpeg": fake_imageio_ffmpeg}):
            self.assertEqual(utils.get_ffmpeg_binary(), "/tmp/bundled-ffmpeg")

    def test_get_effective_video_codec_falls_back_when_encoder_missing(self):
        """
        用户选择的硬件编码器必须先经过 FFmpeg encoder 列表检测。检测不到
        时直接回退 libx264，避免生成任务在写文件阶段才失败。
        """
        config.app["video_codec"] = "h264_nvenc"

        with patch.object(vd, "_ffmpeg_encoder_exists", return_value=False):
            self.assertEqual(vd._get_effective_video_codec(), "libx264")

    def test_get_configured_video_codec_uses_stable_default_when_unset(self):
        """
        WebUI 的“默认”模式不会持久化 video_codec。后端必须在配置缺失时继续
        明确返回 libx264，不能把空值直接交给 MoviePy 或 FFmpeg 自行决定。
        """
        config.app.pop("video_codec", None)

        self.assertEqual(vd._get_configured_video_codec(), "libx264")

    def test_get_configured_video_codec_preserves_explicit_libx264(self):
        """
        用户明确选择 libx264 时需要保持固定选择。它与“跟随项目默认策略”当前
        结果相同，但配置语义不同，未来调整默认值时不能影响显式选择。
        """
        config.app["video_codec"] = "libx264"

        self.assertEqual(vd._get_configured_video_codec(), "libx264")

    def test_ffmpeg_encoder_exists_falls_back_when_probe_fails(self):
        """
        Windows 上用户配置的 ffmpeg 可能因为路径损坏、权限或杀软拦截而无法
        正常执行。encoder 探测失败时必须返回 False，让上层稳定回退 libx264。
        """
        with patch.object(
            vd.subprocess,
            "run",
            side_effect=OSError("permission denied"),
        ):
            self.assertFalse(vd._ffmpeg_encoder_exists("C:/ffmpeg/bin/ffmpeg.exe", "h264_nvenc"))

    def test_write_videofile_falls_back_after_runtime_encoder_failure(self):
        """
        FFmpeg 声明支持某个硬件编码器，不代表当前显卡或驱动一定可用。
        首次实际编码失败后，应立即用 libx264 重试，并在本进程禁用该编码器。
        """

        class _FakeClip:
            def __init__(self):
                self.codecs = []

            def write_videofile(self, output_file, codec, **kwargs):
                self.codecs.append(codec)
                if codec == "h264_nvenc":
                    raise RuntimeError("nvenc device not available")

        fake_clip = _FakeClip()

        with patch.object(vd, "_ffmpeg_encoder_exists", return_value=True):
            used_codec = vd._write_videofile_with_codec_fallback(
                fake_clip,
                "/tmp/fake.mp4",
                codec="h264_nvenc",
                logger=None,
                fps=30,
            )

        self.assertEqual(used_codec, "libx264")
        self.assertEqual(fake_clip.codecs, ["h264_nvenc", "libx264"])
        self.assertIn("h264_nvenc", vd._runtime_disabled_video_codecs)

    def test_write_videofile_does_not_disable_codec_when_fallback_also_fails(self):
        """
        如果 libx264 兜底也失败，失败原因更可能是输出路径、权限、文件占用等
        通用问题，不能误判为硬件编码器不可用。
        """

        class _FakeClip:
            def write_videofile(self, output_file, codec, **kwargs):
                raise RuntimeError(f"{codec} cannot write output")

        with patch.object(vd, "_ffmpeg_encoder_exists", return_value=True):
            with self.assertRaises(RuntimeError):
                vd._write_videofile_with_codec_fallback(
                    _FakeClip(),
                    "/tmp/fake.mp4",
                    codec="h264_nvenc",
                    logger=None,
                    fps=30,
                )

        self.assertNotIn("h264_nvenc", vd._runtime_disabled_video_codecs)

    def test_format_ffmpeg_concat_path_normalizes_windows_path(self):
        """
        concat demuxer 的文件列表对 Windows 反斜杠较敏感，写入 list 前统一
        转成正斜杠，并继续保留单引号转义。
        """
        with patch.object(
            vd.os.path,
            "abspath",
            return_value=r"C:\Users\Test User's Videos\clip.mp4",
        ):
            self.assertEqual(
                vd._format_ffmpeg_concat_path(
                    r"C:\Users\Test User's Videos\clip.mp4"
                ),
                "C:/Users/Test User'\\''s Videos/clip.mp4",
            )

    def test_concat_video_clips_falls_back_after_runtime_encoder_failure(self):
        """
        最终 ffmpeg concat 阶段也要具备同样的回退能力。这里用 mock 模拟
        h264_nvenc 编码失败，确认会自动再用 libx264 执行一次。
        """
        config.app["video_codec"] = "h264_nvenc"

        def fake_run(command, capture_output, text, check):
            codec_index = command.index("-c:v") + 1
            codec = command[codec_index]
            if codec == "h264_nvenc":
                return types.SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="nvenc device not available",
                )
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            clip_file = os.path.join(temp_dir, "clip.mp4")
            output_file = os.path.join(temp_dir, "combined.mp4")
            Path(clip_file).write_bytes(b"fake")

            with patch.object(vd, "_ffmpeg_encoder_exists", return_value=True):
                with patch.object(vd.subprocess, "run", side_effect=fake_run) as run:
                    vd.concat_video_clips_with_ffmpeg(
                        clip_files=[clip_file],
                        output_file=output_file,
                        threads=1,
                        output_dir=temp_dir,
                    )

        used_codecs = [
            call.args[0][call.args[0].index("-c:v") + 1]
            for call in run.call_args_list
        ]
        self.assertEqual(used_codecs, ["h264_nvenc", "libx264"])
        self.assertIn("h264_nvenc", vd._runtime_disabled_video_codecs)

    def test_concat_video_clips_does_not_disable_codec_when_fallback_also_fails(self):
        """
        concat 阶段如果 libx264 也失败，说明可能是输入 list、路径或输出权限
        问题，不能把硬件编码器加入运行时禁用列表。
        """
        config.app["video_codec"] = "h264_nvenc"

        def fake_run(command, capture_output, text, check):
            codec_index = command.index("-c:v") + 1
            codec = command[codec_index]
            return types.SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=f"{codec} cannot write output",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            clip_file = os.path.join(temp_dir, "clip.mp4")
            output_file = os.path.join(temp_dir, "combined.mp4")
            Path(clip_file).write_bytes(b"fake")

            with patch.object(vd, "_ffmpeg_encoder_exists", return_value=True):
                with patch.object(vd.subprocess, "run", side_effect=fake_run):
                    with self.assertRaises(RuntimeError):
                        vd.concat_video_clips_with_ffmpeg(
                            clip_files=[clip_file],
                            output_file=output_file,
                            threads=1,
                            output_dir=temp_dir,
                        )

        self.assertNotIn("h264_nvenc", vd._runtime_disabled_video_codecs)

    def test_open_video_clip_quietly_suppresses_moviepy_stdout(self):
        """
        MoviePy 2.1.x 的 FFMPEG_VideoReader 会直接向 stdout 打印 metadata
        和 ffmpeg 命令。项目服务层应屏蔽这类依赖库噪声，避免用户把
        `audio_found: False` 误判为最终视频没有音频。
        """
        # 测试只关心服务层是否屏蔽 MoviePy 的读取噪声，不应长期保存一份由 PNG
        # 编码而来的二进制 MP4 fixture。运行时生成短视频既能保持测试独立，也能
        # 避免 fixture 因不同编码参数产生帧间闪烁后被误用于视觉效果验证。
        image_path = os.path.join(resources_dir, "1.png")
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "image-fixture.mp4")
            source_clip = ImageClip(image_path).with_duration(0.2)
            try:
                source_clip.write_videofile(
                    video_path,
                    codec="libx264",
                    fps=5,
                    audio=False,
                    logger=None,
                )
            finally:
                source_clip.close()

            stdout = StringIO()
            with redirect_stdout(stdout):
                clip = vd._open_video_clip_quietly(video_path)

            try:
                self.assertEqual(stdout.getvalue(), "")
                self.assertIsNone(clip.audio)
                self.assertGreater(clip.duration, 0)
            finally:
                vd.close_clip(clip)

    def test_combine_videos_closes_audio_clip_when_duration_read_fails(self):
        """
        `combine_videos()` 只需要读取旁白音频时长。即使读取 duration
        时发生异常，也必须关闭 AudioFileClip，避免文件句柄泄漏。
        """

        class _FakeAudioReader:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class _BrokenAudioClip:
            def __init__(self):
                self.reader = _FakeAudioReader()

            @property
            def duration(self):
                raise RuntimeError("failed to read duration")

        fake_audio_clip = _BrokenAudioClip()

        with patch.object(vd, "AudioFileClip", return_value=fake_audio_clip):
            with self.assertRaises(RuntimeError):
                vd.combine_videos(
                    combined_video_path="/tmp/unused-combined.mp4",
                    video_paths=[],
                    audio_file="/tmp/unused-audio.mp3",
                )

        self.assertTrue(fake_audio_clip.reader.closed)

    def test_combine_videos_handles_none_transition_mode(self):
        """
        Ensure `combine_videos` safely handles
        `video_transition_mode=None`.
        """
        class _FakeAudioClip:
            @property
            def duration(self):
                return 10.0

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            combined_video_path = os.path.join(temp_dir, "combined.mp4")
            audio_file = os.path.join(temp_dir, "audio.mp3")

            with patch.object(vd, "AudioFileClip", return_value=_FakeAudioClip()):
                # Use empty video_paths to avoid heavy video processing while
                # still exercising transition mode normalization logic.
                result = vd.combine_videos(
                    combined_video_path=combined_video_path,
                    video_paths=[],
                    audio_file=audio_file,
                    video_transition_mode=None,
                )
                self.assertEqual(result, combined_video_path)

    def _capture_source_ranges_for_clip_speed(
        self,
        *,
        source_duration,
        audio_duration,
        clip_speed,
        max_clip_duration=3,
    ):
        """使用轻量假视频记录 combine_videos 实际读取的源时间范围。"""

        source_ranges = []
        written_durations = []

        class _FakeAudioClip:
            duration = audio_duration

            def close(self):
                pass

        class _FakeVideoClip:
            def __init__(self, duration, records_source_range=False):
                self.duration = duration
                self.size = (1080, 1920)
                self.w = 1080
                self.h = 1920
                self.records_source_range = records_source_range

            def subclipped(self, start_time, end_time):
                # 只记录直接从源文件读取的范围。变速后的安全裁剪也会调用
                # subclipped，但它不代表新的源时间段，不能混入断层判断。
                if self.records_source_range:
                    source_ranges.append((start_time, end_time))
                return _FakeVideoClip(end_time - start_time)

            def with_speed_scaled(self, factor):
                return _FakeVideoClip(self.duration / factor)

            def close(self):
                pass

        def _open_fake_video_clip(_video_path):
            return _FakeVideoClip(source_duration, records_source_range=True)

        def _capture_written_clip(clip, *_args, **_kwargs):
            written_durations.append(clip.duration)

        with tempfile.TemporaryDirectory() as temp_dir:
            combined_video_path = os.path.join(temp_dir, "combined.mp4")
            with (
                patch.object(vd, "AudioFileClip", return_value=_FakeAudioClip()),
                patch.object(
                    vd,
                    "_open_video_clip_quietly",
                    side_effect=_open_fake_video_clip,
                ),
                patch.object(
                    vd,
                    "_write_videofile_with_codec_fallback",
                    side_effect=_capture_written_clip,
                ),
                # random 模式默认会打乱同一源视频的切片。这里保持生成顺序，
                # 才能精确验证相邻源时间段是否连续。
                patch.object(
                    vd,
                    "_prioritize_unique_source_clips",
                    side_effect=lambda subclipped_items, concat_mode: subclipped_items,
                ),
                patch.object(vd, "concat_video_clips_with_ffmpeg"),
                patch.object(vd, "delete_files"),
            ):
                vd.combine_videos(
                    combined_video_path=combined_video_path,
                    video_paths=["clip.mp4"],
                    audio_file="audio.mp3",
                    video_concat_mode=vd.VideoConcatMode.random,
                    max_clip_duration=max_clip_duration,
                    clip_speed=clip_speed,
                )

        return source_ranges, written_durations

    def test_combine_videos_slow_speed_keeps_source_timeline_continuous(self):
        """0.5 倍慢放应连续读取 1.5 秒源片段，不能跳过中间画面。"""

        source_ranges, written_durations = self._capture_source_ranges_for_clip_speed(
            source_duration=4.0,
            audio_duration=5.9,
            clip_speed=0.5,
        )

        self.assertEqual(source_ranges, [(0, 1.5), (1.5, 3.0)])
        self.assertEqual(written_durations, [3.0, 3.0])

    def test_combine_videos_fast_speed_reads_enough_source_content(self):
        """2 倍快放应读取 6 秒源画面，使最终片段仍保持 3 秒。"""

        source_ranges, written_durations = self._capture_source_ranges_for_clip_speed(
            source_duration=8.0,
            audio_duration=2.9,
            clip_speed=2.0,
        )

        self.assertEqual(source_ranges, [(0, 6.0)])
        self.assertEqual(written_durations, [3.0])

    def test_combine_videos_keeps_small_duration_safety_margin(self):
        """
        音频和素材累计时长刚好相等时，仍应继续追加一个短片段作为安全余量。

        FFmpeg 按帧率拼接后可能让最终视频比理论时长短几十毫秒。如果这里
        在 10.0s == 10.0s 时立即停止，成片末尾就可能出现音频还在播放但
        视频素材已经结束的边界问题。
        """

        class _FakeAudioClip:
            duration = 10.0

            def close(self):
                pass

        class _FakeVideoClip:
            def __init__(self, duration):
                self.duration = duration
                self.size = (1080, 1920)
                self.w = 1080
                self.h = 1920

            def subclipped(self, start_time, end_time):
                return _FakeVideoClip(end_time - start_time)

        video_durations = {
            "clip-1.mp4": 3.0,
            "clip-2.mp4": 4.0,
            "clip-3.mp4": 3.0,
            "clip-4.mp4": 2.0,
        }

        def _open_fake_video_clip(video_path):
            return _FakeVideoClip(video_durations[video_path])

        with tempfile.TemporaryDirectory() as temp_dir:
            combined_video_path = os.path.join(temp_dir, "combined.mp4")

            with patch.object(vd, "AudioFileClip", return_value=_FakeAudioClip()):
                with patch.object(
                    vd, "_open_video_clip_quietly", side_effect=_open_fake_video_clip
                ):
                    with patch.object(
                        vd, "_write_videofile_with_codec_fallback"
                    ) as write_mock:
                        with patch.object(vd, "concat_video_clips_with_ffmpeg") as concat_mock:
                            with patch.object(vd, "delete_files"):
                                result = vd.combine_videos(
                                    combined_video_path=combined_video_path,
                                    video_paths=list(video_durations.keys()),
                                    audio_file=os.path.join(temp_dir, "audio.mp3"),
                                    video_aspect=vd.VideoAspect.portrait,
                                    video_concat_mode=vd.VideoConcatMode.sequential,
                                    video_transition_mode=None,
                                    max_clip_duration=10,
                                )

        self.assertEqual(result, combined_video_path)
        self.assertEqual(write_mock.call_count, 4)
        self.assertEqual(concat_mock.call_args.kwargs["max_duration"], 10.0)

    def test_concat_video_clips_limits_output_to_audio_duration(self):
        """最终拼接时应裁到音频时长，避免安全余量带来明显静音尾巴。"""

        def fake_run(command, capture_output, text, check):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            clip_file = os.path.join(temp_dir, "clip.mp4")
            output_file = os.path.join(temp_dir, "combined.mp4")
            Path(clip_file).write_bytes(b"fake")

            with patch.object(vd.subprocess, "run", side_effect=fake_run) as run:
                vd.concat_video_clips_with_ffmpeg(
                    clip_files=[clip_file],
                    output_file=output_file,
                    threads=1,
                    output_dir=temp_dir,
                    max_duration=10.0,
                )

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-t") + 1], "10.000")
        self.assertLess(command.index("-t"), command.index(output_file))

    def test_prioritize_unique_source_clips_uses_each_source_before_reuse(self):
        """
        随机模式下，一个长素材会被拆成多个片段。调度层应先让每个源素材
        至少出现一次，再使用同一源素材的其他切片，降低用户感知到的重复。
        """
        clips = [
            vd.SubClippedVideoClip("a.mp4", 0, 4, source_file_path="a.mp4"),
            vd.SubClippedVideoClip("a.mp4", 4, 8, source_file_path="a.mp4"),
            vd.SubClippedVideoClip("b.mp4", 0, 4, source_file_path="b.mp4"),
            vd.SubClippedVideoClip("b.mp4", 4, 8, source_file_path="b.mp4"),
            vd.SubClippedVideoClip("c.mp4", 0, 4, source_file_path="c.mp4"),
        ]

        ordered_clips = vd._prioritize_unique_source_clips(
            subclipped_items=clips,
            concat_mode=vd.VideoConcatMode.random,
        )

        self.assertCountEqual(ordered_clips, clips)
        first_round_sources = [clip.source_file_path for clip in ordered_clips[:3]]
        self.assertCountEqual(first_round_sources, ["a.mp4", "b.mp4", "c.mp4"])

    def test_prioritize_unique_source_clips_keeps_sequential_order(self):
        """
        顺序模式本身只取每个素材的首段，不应被随机调度逻辑改变顺序。
        """
        clips = [
            vd.SubClippedVideoClip("a.mp4", 0, 4, source_file_path="a.mp4"),
            vd.SubClippedVideoClip("b.mp4", 0, 4, source_file_path="b.mp4"),
            vd.SubClippedVideoClip("c.mp4", 0, 4, source_file_path="c.mp4"),
        ]

        ordered_clips = vd._prioritize_unique_source_clips(
            subclipped_items=clips,
            concat_mode=vd.VideoConcatMode.sequential,
        )

        self.assertEqual(ordered_clips, clips)

    def test_prioritize_unique_source_clips_prefers_long_primary_clip(self):
        """
        同一个源素材的最后一个切片可能短于目标片段时长。首轮去重时应优先
        选择较长片段，否则会因为累计时长不足而提前复用素材。
        """
        short_tail = vd.SubClippedVideoClip(
            "a.mp4", 6, 6.5, source_file_path="a.mp4"
        )
        full_clip = vd.SubClippedVideoClip(
            "a.mp4", 0, 3, source_file_path="a.mp4"
        )
        other_source = vd.SubClippedVideoClip(
            "b.mp4", 0, 3, source_file_path="b.mp4"
        )

        ordered_clips = vd._prioritize_unique_source_clips(
            subclipped_items=[short_tail, full_clip, other_source],
            concat_mode=vd.VideoConcatMode.random,
        )

        first_a_clip = next(
            clip for clip in ordered_clips if clip.source_file_path == "a.mp4"
        )
        self.assertEqual(first_a_clip, full_clip)
    
    def test_wrap_text(self):
        """test text wrapping function"""
        try:
            font_path = os.path.join(utils.font_dir(), "STHeitiMedium.ttc")
            if not os.path.exists(font_path):
                self.fail(f"font file not found: {font_path}")
                
            # test english text wrapping
            test_text_en = "This is a test text for wrapping long sentences in english language"
            
            wrapped_text_en, text_height_en = vd.wrap_text(
                text=test_text_en,
                max_width=300,
                font=font_path,
                fontsize=30
            )
            print(wrapped_text_en, text_height_en)
            # verify text is wrapped
            self.assertIn("\n", wrapped_text_en)
            
            # test chinese text wrapping
            test_text_zh = "这是一段用来测试中文长句换行的文本内容，应该会根据宽度限制进行换行处理"
            wrapped_text_zh, text_height_zh = vd.wrap_text(
                text=test_text_zh,
                max_width=300,
                font=font_path,
                fontsize=30
            )   
            print(wrapped_text_zh, text_height_zh)
            # verify chinese text is wrapped
            self.assertIn("\n", wrapped_text_zh)
        except Exception as e:
            self.fail(f"test wrap_text failed: {str(e)}")

    def test_rounded_subtitle_background_clip_has_transparent_corners(self):
        """
        圆角字幕背景只在用户显式开启时使用。这里直接验证生成的 RGBA
        背景具备透明圆角和半透明中心，避免后续改动把圆角效果退化成实心矩形。
        """
        clip = vd._rounded_subtitle_background_clip(
            width=120,
            height=48,
            color="#123456",
            alpha=140,
            radius=16,
        )
        try:
            frame = clip.get_frame(0)
            mask = clip.mask.get_frame(0)

            self.assertEqual(frame.shape[0:2], (48, 120))
            self.assertEqual(tuple(frame[24, 60]), (18, 52, 86))
            self.assertEqual(mask[0, 0], 0)
            self.assertGreater(mask[24, 60], 0.5)
            self.assertLess(mask[24, 60], 0.6)
        finally:
            clip.close()

    def test_get_temp_audio_dir_returns_system_temp_on_windows(self):
        with patch("sys.platform", "win32"):
            result = vd._get_temp_audio_dir("/some/output/dir")
            self.assertEqual(result, tempfile.gettempdir())

    def test_get_temp_audio_dir_returns_output_dir_on_non_windows(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                with patch("sys.platform", platform):
                    result = vd._get_temp_audio_dir("/some/output/dir")
                    self.assertEqual(result, "/some/output/dir")


class TestMaterialResolutionTolerance(unittest.TestCase):
    def test_accepts_material_at_the_nominal_minimum(self):
        self.assertTrue(vd.is_material_resolution_acceptable(480, 480))

    def test_accepts_whatsapp_recompressed_portrait_clip(self):
        # WhatsApp delivers 9:16 clips as 478x850, two pixels under the
        # nominal 480 minimum. Rejecting them fails the whole task.
        self.assertTrue(vd.is_material_resolution_acceptable(478, 850))

    def test_accepts_material_exactly_at_the_tolerance_bound(self):
        bound = vd._MIN_MATERIAL_DIMENSION - vd._MIN_DIMENSION_TOLERANCE
        self.assertTrue(vd.is_material_resolution_acceptable(bound, bound))

    def test_rejects_material_just_below_the_tolerance_bound(self):
        bound = vd._MIN_MATERIAL_DIMENSION - vd._MIN_DIMENSION_TOLERANCE
        self.assertFalse(vd.is_material_resolution_acceptable(bound - 1, 850))
        self.assertFalse(vd.is_material_resolution_acceptable(850, bound - 1))

    def test_rejects_genuinely_low_resolution_material(self):
        self.assertFalse(vd.is_material_resolution_acceptable(320, 240))


if __name__ == "__main__":
    unittest.main()
