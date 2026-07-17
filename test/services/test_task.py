import unittest
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import task as tm
from app.models.schema import MaterialInfo, VideoParams
from app.utils import utils

resources_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")
RUN_INTEGRATION_TESTS = os.environ.get("MPT_RUN_INTEGRATION_TESTS", "").lower() in {
    "1",
    "true",
    "yes",
}


def _mock_call_text(*mocks):
    entries = []
    for mocked_logger in mocks:
        for item in mocked_logger.call_args_list:
            entries.append(" ".join(str(value) for value in item.args))
            entries.append(str(item.kwargs))
    return "\n".join(entries)


class TestTaskService(unittest.TestCase):
    def setUp(self):
        pass
    
    def tearDown(self):
        pass

    def test_generate_script_forwards_advanced_prompt_options(self):
        """
        任务生成入口和 WebUI/API 共用 VideoParams。这里验证自动生成文案时，
        高级提示词参数会继续传到 LLM 服务层，避免只在 /scripts 接口生效。
        """
        params = VideoParams(
            video_subject="咖啡",
            video_script="",
            video_language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

        with patch.object(tm.llm, "generate_script", return_value="生成的文案") as generate:
            result = tm.generate_script("task-id", params)

        self.assertEqual(result, "生成的文案")
        generate.assert_called_once_with(
            video_subject="咖啡",
            language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

    def test_generate_final_videos_forwards_clip_speed(self):
        """任务编排层必须把用户选择的画面速度传给视频合成服务。"""
        params = VideoParams(
            video_subject="test",
            video_count=1,
            video_clip_speed=1.25,
        )

        with (
            patch.object(tm.video, "combine_videos") as combine_videos,
            patch.object(tm.video, "combine_scene_videos") as combine_scenes,
            patch.object(tm.video, "generate_video"),
            patch.object(tm.sm.state, "update_task"),
        ):
            tm.generate_final_videos(
                task_id="clip-speed-task",
                params=params,
                downloaded_videos=["material.mp4"],
                audio_file="audio.mp3",
                subtitle_path="",
            )

        self.assertEqual(combine_videos.call_args.kwargs["clip_speed"], 1.25)
        combine_scenes.assert_not_called()

    def test_generate_final_videos_dispatches_markdown_scene_materials(self):
        params = VideoParams(video_subject="test", video_count=1)
        scene_materials = [
            SimpleNamespace(
                scene_index=1,
                required_duration=2.0,
                video_paths=("scene.mp4",),
            )
        ]

        with (
            patch.object(tm.video, "combine_scene_videos") as combine_scenes,
            patch.object(tm.video, "combine_videos") as combine_legacy,
            patch.object(tm.video, "generate_video"),
            patch.object(tm.sm.state, "update_task"),
        ):
            tm.generate_final_videos(
                task_id="markdown-scenes",
                params=params,
                downloaded_videos=["scene.mp4"],
                audio_file="audio.mp3",
                subtitle_path="",
                scene_materials=scene_materials,
            )

        combine_scenes.assert_called_once()
        self.assertIs(combine_scenes.call_args.kwargs["scene_plans"], scene_materials)
        combine_legacy.assert_not_called()

    def test_save_script_data_legacy_path_uses_utils_serializer(self):
        params = VideoParams(video_subject="legacy")

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(tm.utils, "to_json", return_value="{}") as serialize,
            ):
                tm.save_script_data(
                    "legacy-script",
                    "legacy script",
                    ["legacy term"],
                    params,
                )

        serialize.assert_called_once()

    def test_start_writes_emphasis_manifest_and_passes_it_to_video(self):
        params = VideoParams(
            video_subject="婚姻",
            video_script="婚姻不是逃避吃苦而编出来的段子。",
            emphasis_enabled=True,
            emphasis_terms="不是逃避,吃苦",
        )

        with tempfile.TemporaryDirectory() as task_dir:
            subtitle_path = Path(task_dir) / "subtitle.srt"
            subtitle_path.write_text(
                "1\n00:00:00,000 --> 00:00:03,000\n婚姻不是逃避吃苦而编出来的段子\n\n",
                encoding="utf-8",
            )
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(tm, "generate_terms", return_value=["marriage"]),
                patch.object(tm, "save_script_data"),
                patch.object(tm, "generate_audio", return_value=("audio.wav", 3, object())),
                patch.object(tm, "generate_subtitle", return_value=str(subtitle_path)),
                patch.object(tm, "get_video_materials", return_value=["material.mp4"]),
                patch.object(tm.video, "combine_videos"),
                patch.object(tm.video, "generate_video") as generate_video,
                patch.object(
                    tm.upload_post.upload_post_service,
                    "is_configured",
                    return_value=False,
                ),
                patch.object(tm.sm.state, "update_task"),
            ):
                result = tm.start("task-emphasis", params)

            manifest = Path(task_dir) / "emphasis.json"
            self.assertTrue(manifest.is_file())
            self.assertEqual(result["emphasis_path"], str(manifest))
            self.assertEqual(
                generate_video.call_args.kwargs["emphasis_path"], str(manifest)
            )

    def test_markdown_task_bypasses_llm_and_uses_scene_pipeline(self):
        params = VideoParams(
            video_subject="will be replaced",
            video_source="pixabay",
            subtitle_enabled=False,
            emphasis_enabled=True,
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": ["婚姻"],
                        "material_search_terms": ["wedding couple"],
                    },
                    {
                        "number": 2,
                        "text": "婚姻也需要耐心。",
                        "emphasis_terms": ["婚姻"],
                        "material_search_terms": [],
                    },
                ],
            },
        )
        subtitles = [
            (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通"),
            (2, "00:00:02,000 --> 00:00:04,000", "婚姻也需要耐心"),
        ]
        scene_materials = [
            SimpleNamespace(
                scene_index=1,
                first_row=1,
                last_row=2,
                start=0.0,
                end=21.11,
                required_duration=21.11,
                search_terms=("wedding couple",),
                video_paths=("scene.mp4",),
            )
        ]

        def fail_llm(*args, **kwargs):
            raise AssertionError("Markdown task called an LLM")

        with tempfile.TemporaryDirectory() as task_dir:
            alignment_path = str(Path(task_dir) / "alignment.srt")
            subtitle_params = []

            def generate_alignment(task_id, runtime_params, script, sub_maker, audio):
                subtitle_params.append(runtime_params)
                return alignment_path

            service = tm.upload_post.upload_post_service
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(tm.llm, "generate_script", side_effect=fail_llm),
                patch.object(tm.llm, "generate_terms", side_effect=fail_llm),
                patch.object(tm.llm, "generate_emphasis_terms", side_effect=fail_llm),
                patch.object(tm.llm, "generate_social_metadata", side_effect=fail_llm),
                patch.object(
                    tm.utils,
                    "to_json",
                    wraps=tm.utils.to_json,
                ) as legacy_serializer,
                patch.object(
                    tm,
                    "generate_audio",
                    return_value=("audio.wav", 22.0, object()),
                ),
                patch.object(
                    tm.voice,
                    "get_audio_duration",
                    return_value=21.01,
                ) as exact_audio_duration,
                patch.object(
                    tm.video,
                    "get_required_video_duration",
                    wraps=tm.video.get_required_video_duration,
                ) as required_video_duration,
                patch.object(tm, "generate_subtitle", side_effect=generate_alignment),
                patch.object(
                    tm.subtitle,
                    "file_to_subtitles",
                    return_value=subtitles,
                ) as read_subtitles,
                patch.object(
                    tm.material,
                    "download_scene_materials",
                    return_value=scene_materials,
                ) as download,
                patch.object(
                    tm,
                    "generate_final_videos",
                    return_value=(["final.mp4"], ["combined.mp4"]),
                ) as final,
                patch.object(service, "is_configured", return_value=True),
                patch.object(service, "auto_upload", True),
                patch.object(service, "platforms", ["youtube"]),
                patch.object(service, "youtube_privacy_status", "unlisted"),
                patch.object(
                    tm.upload_post,
                    "cross_post_video",
                    return_value={"success": True},
                ) as cross_post,
                patch.object(tm.sm.state, "update_task"),
            ):
                result = tm.start("markdown-task", params)

            self.assertEqual(result["script"], "婚姻需要沟通。\n婚姻也需要耐心。")
            legacy_serializer.assert_not_called()
            self.assertEqual(result["materials"], ["scene.mp4"])
            self.assertEqual(result["terms"], ["wedding couple"])
            download.assert_called_once()
            timed_scenes = download.call_args.kwargs["scenes"]
            self.assertEqual(len(timed_scenes), 1)
            self.assertEqual(timed_scenes[0].start, 0.0)
            self.assertAlmostEqual(timed_scenes[0].end, 21.11)
            self.assertAlmostEqual(
                sum(scene.end - scene.start for scene in timed_scenes),
                21.11,
            )
            exact_audio_duration.assert_called_once_with("audio.wav")
            required_video_duration.assert_called_once_with(21.01)
            read_subtitles.assert_called_once_with(alignment_path)
            self.assertIs(final.call_args.kwargs["scene_materials"], scene_materials)
            self.assertEqual(final.call_args.kwargs["subtitle_path"], "")
            self.assertEqual(len(subtitle_params), 1)
            self.assertTrue(subtitle_params[0].subtitle_enabled)
            self.assertFalse(params.subtitle_enabled)
            self.assertTrue(Path(result["emphasis_path"]).is_file())

            youtube_extra = cross_post.call_args.kwargs["youtube_extra"]
            self.assertEqual(
                youtube_extra,
                {
                    "youtube_title": "婚姻二字",
                    "youtube_description": "",
                    "tags": [],
                    "privacyStatus": "unlisted",
                    "containsSyntheticMedia": True,
                },
            )

            script_data = json.loads(
                (Path(task_dir) / "script.json").read_text(encoding="utf-8")
            )
            restored = VideoParams.model_validate(script_data["params"])
            self.assertEqual(restored.markdown_script.title, "婚姻二字")
            self.assertEqual(restored.markdown_script.rows[1].number, 2)

            scene_manifest = json.loads(
                (Path(task_dir) / "scene-materials.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                scene_manifest,
                [
                    {
                        "scene_index": 1,
                        "first_row": 1,
                        "last_row": 2,
                        "start": 0.0,
                        "end": 21.11,
                        "required_duration": 21.11,
                        "search_terms": ["wedding couple"],
                        "video_paths": ["scene.mp4"],
                    }
                ],
            )

    def test_markdown_task_rejects_invalid_exact_audio_duration_before_download(self):
        invalid_durations = {
            "zero": 0.0,
            "nan": float("nan"),
            "infinity": float("inf"),
        }

        for label, exact_duration in invalid_durations.items():
            with self.subTest(duration=label):
                params = VideoParams(
                    video_subject="婚姻二字",
                    video_source="pixabay",
                    markdown_script={
                        "title": "婚姻二字",
                        "rows": [
                            {
                                "number": 1,
                                "text": "婚姻需要沟通。",
                                "emphasis_terms": [],
                                "material_search_terms": ["wedding couple"],
                            }
                        ],
                    },
                )
                subtitles = [
                    (1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通")
                ]

                with tempfile.TemporaryDirectory() as task_dir:
                    with (
                        patch.object(tm.utils, "task_dir", return_value=task_dir),
                        patch.object(
                            tm,
                            "generate_audio",
                            return_value=("audio.wav", 22.0, object()),
                        ),
                        patch.object(
                            tm.voice,
                            "get_audio_duration",
                            return_value=exact_duration,
                        ) as probe_duration,
                        patch.object(
                            tm.video,
                            "get_required_video_duration",
                            wraps=tm.video.get_required_video_duration,
                        ) as target_duration,
                        patch.object(
                            tm,
                            "generate_subtitle",
                            return_value="alignment.srt",
                        ) as generate_subtitle,
                        patch.object(
                            tm.subtitle,
                            "file_to_subtitles",
                            return_value=subtitles,
                        ),
                        patch.object(
                            tm.material,
                            "download_scene_materials",
                            return_value=[],
                        ) as download,
                        patch.object(tm, "generate_final_videos") as final,
                        patch.object(tm.sm.state, "update_task") as update_task,
                    ):
                        result = tm.start(f"markdown-invalid-audio-{label}", params)

                self.assertIsNone(result)
                probe_duration.assert_called_once_with("audio.wav")
                target_duration.assert_not_called()
                generate_subtitle.assert_not_called()
                download.assert_not_called()
                final.assert_not_called()
                failed_updates = [
                    item
                    for item in update_task.call_args_list
                    if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
                ]
                self.assertEqual(len(failed_updates), 1)

    def test_markdown_api_document_without_scenes_fails_once_before_download(self):
        params = VideoParams(
            video_subject="无素材场景",
            video_source="pixabay",
            markdown_script={
                "title": "无素材场景",
                "rows": [
                    {
                        "number": 1,
                        "text": "这一行没有素材搜索词。",
                        "emphasis_terms": [],
                        "material_search_terms": [],
                    }
                ],
            },
        )
        subtitles = [
            (1, "00:00:00,000 --> 00:00:02,000", "这一行没有素材搜索词")
        ]

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(
                    tm,
                    "generate_audio",
                    return_value=("audio.wav", 2.0, object()),
                ),
                patch.object(tm.voice, "get_audio_duration", return_value=2.0),
                patch.object(tm, "generate_subtitle", return_value="alignment.srt"),
                patch.object(
                    tm.subtitle,
                    "file_to_subtitles",
                    return_value=subtitles,
                ),
                patch.object(tm.material, "download_scene_materials") as download,
                patch.object(tm, "generate_final_videos") as final,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                result = tm.start("markdown-no-scenes", params)

        self.assertIsNone(result)
        download.assert_not_called()
        final.assert_not_called()
        failed_updates = [
            item
            for item in update_task.call_args_list
            if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
        ]
        self.assertEqual(len(failed_updates), 1)

    def test_markdown_api_rejects_a_missing_first_scene_before_the_pipeline(self):
        params = VideoParams(
            video_subject="首场景缺失",
            video_source="pixabay",
            markdown_script={
                "title": "首场景缺失",
                "rows": [
                    {
                        "number": 1,
                        "text": "第一行没有素材搜索词。",
                        "emphasis_terms": [],
                        "material_search_terms": [],
                    },
                    {
                        "number": 2,
                        "text": "第二行才提供素材搜索词。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding"],
                    },
                ],
            },
        )
        subtitles = [
            (1, "00:00:00,000 --> 00:00:01,000", "第一行没有素材搜索词"),
            (2, "00:00:01,000 --> 00:00:02,000", "第二行才提供素材搜索词"),
        ]

        with (
            patch.object(tm.script_document, "apply_to_video_params") as apply_params,
            patch.object(tm, "save_script_data") as save_script,
            patch.object(
                tm,
                "generate_audio",
                return_value=("audio.wav", 2.0, object()),
            ) as generate_audio,
            patch.object(tm.voice, "get_audio_duration", return_value=2.0),
            patch.object(
                tm,
                "generate_subtitle",
                return_value="alignment.srt",
            ) as generate_subtitle,
            patch.object(tm.subtitle, "file_to_subtitles", return_value=subtitles),
            patch.object(
                tm.material,
                "download_scene_materials",
                return_value=[],
            ) as download,
            patch.object(tm, "generate_final_videos") as final,
            patch.object(tm.sm.state, "update_task") as update_task,
        ):
            result = tm.start("markdown-missing-first-scene", params)

        self.assertIsNone(result)
        apply_params.assert_not_called()
        save_script.assert_not_called()
        generate_audio.assert_not_called()
        generate_subtitle.assert_not_called()
        download.assert_not_called()
        final.assert_not_called()
        failed_updates = [
            item
            for item in update_task.call_args_list
            if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
        ]
        self.assertEqual(len(failed_updates), 1)

    def test_markdown_task_fails_when_emphasis_manifest_cannot_be_written(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="pixabay",
            emphasis_enabled=True,
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": ["婚姻"],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )
        subtitles = [(1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通")]
        private_path = r"C:\Users\private-speaker\Documents\voice\emphasis.json"
        write_error = OSError(f"access denied for {private_path}")

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(
                    tm,
                    "generate_audio",
                    return_value=("audio.wav", 2.0, object()),
                ),
                patch.object(tm.voice, "get_audio_duration", return_value=2.0),
                patch.object(tm, "generate_subtitle", return_value="alignment.srt"),
                patch.object(tm.subtitle, "file_to_subtitles", return_value=subtitles),
                patch.object(
                    tm.emphasis,
                    "write_emphasis_cues",
                    side_effect=write_error,
                ),
                patch.object(tm.material, "download_scene_materials") as download,
                patch.object(tm, "generate_final_videos") as final,
                patch.object(tm.upload_post, "cross_post_video") as cross_post,
                patch.object(tm.logger, "error") as log_error,
                patch.object(tm.logger, "warning") as log_warning,
                patch.object(tm.logger, "exception") as log_exception,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                result = tm.start("markdown-emphasis-write-error", params)

        self.assertIsNone(result)
        failed_updates = [
            item
            for item in update_task.call_args_list
            if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
        ]
        self.assertEqual(len(failed_updates), 1)
        log_text = _mock_call_text(log_error, log_warning, log_exception)
        self.assertIn("emphasis manifest", log_text)
        self.assertIn("emphasis.json", log_text)
        self.assertIn("OSError", log_text)
        self.assertNotIn(str(write_error), log_text)
        self.assertNotIn(private_path, log_text)
        update_text = str(update_task.call_args_list)
        self.assertNotIn(str(write_error), update_text)
        self.assertNotIn(private_path, update_text)
        log_exception.assert_not_called()
        download.assert_not_called()
        final.assert_not_called()
        cross_post.assert_not_called()

    def test_markdown_task_fails_when_scene_manifest_cannot_be_written(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="pixabay",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )
        subtitles = [(1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通")]
        scene_materials = [
            SimpleNamespace(
                scene_index=1,
                first_row=1,
                last_row=1,
                start=0.0,
                end=2.0,
                required_duration=2.0,
                search_terms=("wedding couple",),
                video_paths=("scene.mp4",),
            )
        ]
        private_url = (
            "https://media.example.test/video.mp4?"
            "api_key=FAKE_SCENE_SECRET_456&user=private"
        )
        write_error = OSError(f"request failed for {private_url}")
        real_open = open

        def fail_only_scene_manifest(file, *args, **kwargs):
            if Path(file).name == "scene-materials.json":
                raise write_error
            return real_open(file, *args, **kwargs)

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(
                    tm,
                    "generate_audio",
                    return_value=("audio.wav", 2.0, object()),
                ),
                patch.object(tm.voice, "get_audio_duration", return_value=2.0),
                patch.object(tm, "generate_subtitle", return_value="alignment.srt"),
                patch.object(tm.subtitle, "file_to_subtitles", return_value=subtitles),
                patch.object(
                    tm.material,
                    "download_scene_materials",
                    return_value=scene_materials,
                ),
                patch.object(
                    tm.utils,
                    "to_json",
                    wraps=tm.utils.to_json,
                ) as legacy_serializer,
                patch("builtins.open", side_effect=fail_only_scene_manifest),
                patch.object(tm, "generate_final_videos") as final,
                patch.object(tm.upload_post, "cross_post_video") as cross_post,
                patch.object(tm.logger, "error") as log_error,
                patch.object(tm.logger, "warning") as log_warning,
                patch.object(tm.logger, "exception") as log_exception,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                result = tm.start("markdown-scene-manifest-write-error", params)

            self.assertTrue((Path(task_dir) / "script.json").is_file())

        self.assertIsNone(result)
        failed_updates = [
            item
            for item in update_task.call_args_list
            if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
        ]
        self.assertEqual(len(failed_updates), 1)
        legacy_serializer.assert_not_called()
        log_text = _mock_call_text(log_error, log_warning, log_exception)
        self.assertIn("scene manifest", log_text)
        self.assertIn("scene-materials.json", log_text)
        self.assertIn("OSError", log_text)
        self.assertNotIn(str(write_error), log_text)
        self.assertNotIn(private_url, log_text)
        self.assertNotIn("FAKE_SCENE_SECRET_456", log_text)
        update_text = str(update_task.call_args_list)
        self.assertNotIn(str(write_error), update_text)
        self.assertNotIn(private_url, update_text)
        log_exception.assert_not_called()
        final.assert_not_called()
        cross_post.assert_not_called()

    def test_markdown_task_fails_when_script_manifest_cannot_be_written(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="pixabay",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )
        secret_key = "FAKE_SCRIPT_API_KEY_123"
        write_error = OSError(f"permission denied with api_key={secret_key}")
        real_open = open

        def fail_only_script_manifest(file, *args, **kwargs):
            if Path(file).name == "script.json":
                raise write_error
            return real_open(file, *args, **kwargs)

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(
                    tm.utils,
                    "to_json",
                    wraps=tm.utils.to_json,
                ) as legacy_serializer,
                patch("builtins.open", side_effect=fail_only_script_manifest),
                patch.object(tm, "generate_audio") as generate_audio,
                patch.object(tm, "generate_final_videos") as final,
                patch.object(tm.upload_post, "cross_post_video") as cross_post,
                patch.object(tm.logger, "error") as log_error,
                patch.object(tm.logger, "warning") as log_warning,
                patch.object(tm.logger, "exception") as log_exception,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                result = tm.start("markdown-script-manifest-write-error", params)

        self.assertIsNone(result)
        failed_updates = [
            item
            for item in update_task.call_args_list
            if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
        ]
        self.assertEqual(len(failed_updates), 1)
        legacy_serializer.assert_not_called()
        log_text = _mock_call_text(log_error, log_warning, log_exception)
        self.assertIn("script manifest", log_text)
        self.assertIn("script.json", log_text)
        self.assertIn("OSError", log_text)
        self.assertNotIn(str(write_error), log_text)
        self.assertNotIn(secret_key, log_text)
        update_text = str(update_task.call_args_list)
        self.assertNotIn(str(write_error), update_text)
        self.assertNotIn(secret_key, update_text)
        log_exception.assert_not_called()
        generate_audio.assert_not_called()
        final.assert_not_called()
        cross_post.assert_not_called()

    def test_markdown_task_redacts_scene_manifest_serialization_error(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="pixabay",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )
        subtitles = [(1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通")]
        scene_materials = [
            SimpleNamespace(
                scene_index=1,
                first_row=1,
                last_row=1,
                start=0.0,
                end=2.0,
                required_duration=2.0,
                search_terms=("wedding couple",),
                video_paths=("scene.mp4",),
            )
        ]
        secret_key = "FAKE_SERIALIZATION_SECRET_789"
        private_path = r"C:\Users\private-editor\Documents\scene.json"
        private_url = "https://example.test/scene?token=private-query-token"
        serialization_error = ValueError(
            f"cannot encode {secret_key} from {private_path} via {private_url}"
        )
        real_json_dumps = json.dumps

        def fail_only_scene_manifest_serialization(value, *args, **kwargs):
            if isinstance(value, list):
                raise serialization_error
            return real_json_dumps(value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(
                    tm,
                    "generate_audio",
                    return_value=("audio.wav", 2.0, object()),
                ),
                patch.object(tm.voice, "get_audio_duration", return_value=2.0),
                patch.object(tm, "generate_subtitle", return_value="alignment.srt"),
                patch.object(tm.subtitle, "file_to_subtitles", return_value=subtitles),
                patch.object(
                    tm.material,
                    "download_scene_materials",
                    return_value=scene_materials,
                ),
                patch.object(
                    tm.json,
                    "dumps",
                    side_effect=fail_only_scene_manifest_serialization,
                ),
                patch.object(
                    tm.utils,
                    "to_json",
                    wraps=tm.utils.to_json,
                ) as legacy_serializer,
                patch.object(tm, "generate_final_videos") as final,
                patch.object(tm.upload_post, "cross_post_video") as cross_post,
                patch.object(tm.logger, "error") as log_error,
                patch.object(tm.logger, "warning") as log_warning,
                patch.object(tm.logger, "exception") as log_exception,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                result = tm.start("markdown-scene-serialization-error", params)

            self.assertTrue((Path(task_dir) / "script.json").is_file())

        self.assertIsNone(result)
        failed_updates = [
            item
            for item in update_task.call_args_list
            if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
        ]
        self.assertEqual(len(failed_updates), 1)
        legacy_serializer.assert_not_called()
        log_text = _mock_call_text(log_error, log_warning, log_exception)
        self.assertIn("scene manifest", log_text)
        self.assertIn("scene-materials.json", log_text)
        self.assertIn("ValueError", log_text)
        self.assertNotIn(str(serialization_error), log_text)
        self.assertNotIn(secret_key, log_text)
        self.assertNotIn(private_path, log_text)
        self.assertNotIn(private_url, log_text)
        update_text = str(update_task.call_args_list)
        self.assertNotIn(str(serialization_error), update_text)
        self.assertNotIn(secret_key, update_text)
        self.assertNotIn(private_path, update_text)
        self.assertNotIn(private_url, update_text)
        log_exception.assert_not_called()
        final.assert_not_called()
        cross_post.assert_not_called()

    def test_markdown_task_rejects_nan_in_scene_manifest_safely(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="pixabay",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )
        subtitles = [(1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通")]
        scene_materials = [
            SimpleNamespace(
                scene_index=1,
                first_row=1,
                last_row=1,
                start=float("nan"),
                end=2.0,
                required_duration=2.0,
                search_terms=("wedding couple",),
                video_paths=("scene.mp4",),
            )
        ]

        with tempfile.TemporaryDirectory() as task_dir:
            service = tm.upload_post.upload_post_service
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(
                    tm,
                    "generate_audio",
                    return_value=("audio.wav", 2.0, object()),
                ),
                patch.object(tm.voice, "get_audio_duration", return_value=2.0),
                patch.object(tm, "generate_subtitle", return_value="alignment.srt"),
                patch.object(tm.subtitle, "file_to_subtitles", return_value=subtitles),
                patch.object(
                    tm.material,
                    "download_scene_materials",
                    return_value=scene_materials,
                ),
                patch.object(
                    tm.utils,
                    "to_json",
                    wraps=tm.utils.to_json,
                ) as legacy_serializer,
                patch.object(
                    tm,
                    "generate_final_videos",
                    return_value=(["final.mp4"], ["combined.mp4"]),
                ) as final,
                patch.object(service, "is_configured", return_value=False),
                patch.object(tm.logger, "error") as log_error,
                patch.object(tm.logger, "warning") as log_warning,
                patch.object(tm.logger, "exception") as log_exception,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                result = tm.start("markdown-scene-nan", params)

        self.assertIsNone(result)
        failed_updates = [
            item
            for item in update_task.call_args_list
            if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
        ]
        self.assertEqual(len(failed_updates), 1)
        legacy_serializer.assert_not_called()
        log_text = _mock_call_text(log_error, log_warning, log_exception)
        self.assertIn("scene manifest", log_text)
        self.assertIn("ValueError", log_text)
        log_exception.assert_not_called()
        final.assert_not_called()

    def test_markdown_strict_serializer_propagates_unexpected_runtime_error(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="pixabay",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )
        secret_key = "FAKE_RUNTIME_SECRET_012"
        runtime_error = RuntimeError(f"unexpected serializer failure {secret_key}")

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(tm.json, "dumps", side_effect=runtime_error),
                patch.object(
                    tm.utils,
                    "to_json",
                    wraps=tm.utils.to_json,
                ) as legacy_serializer,
                patch.object(tm, "generate_audio") as generate_audio,
                patch.object(tm.logger, "error") as log_error,
                patch.object(tm.logger, "warning") as log_warning,
                patch.object(tm.logger, "exception") as log_exception,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                with self.assertRaises(RuntimeError) as raised:
                    tm.start("markdown-runtime-serializer-error", params)

        self.assertIs(raised.exception, runtime_error)
        legacy_serializer.assert_not_called()
        log_text = _mock_call_text(log_error, log_warning, log_exception)
        self.assertNotIn(secret_key, log_text)
        log_exception.assert_not_called()
        generate_audio.assert_not_called()
        failed_updates = [
            item
            for item in update_task.call_args_list
            if item.kwargs.get("state") == tm.const.TASK_STATE_FAILED
        ]
        self.assertEqual(failed_updates, [])

    def test_markdown_task_rejects_local_materials_without_llm(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="local",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )

        def fail_llm(*args, **kwargs):
            raise AssertionError("Markdown task called an LLM")

        with (
            patch.object(tm.llm, "generate_script", side_effect=fail_llm),
            patch.object(tm.llm, "generate_terms", side_effect=fail_llm),
            patch.object(tm.llm, "generate_emphasis_terms", side_effect=fail_llm),
            patch.object(tm.llm, "generate_social_metadata", side_effect=fail_llm),
            patch.object(tm, "generate_audio") as generate_audio,
            patch.object(tm.material, "download_scene_materials") as download,
            patch.object(tm.sm.state, "update_task") as update_task,
        ):
            result = tm.start("markdown-local", params)

        self.assertIsNone(result)
        generate_audio.assert_not_called()
        download.assert_not_called()
        update_task.assert_any_call("markdown-local", state=tm.const.TASK_STATE_FAILED)

    def test_markdown_task_does_not_fall_back_when_scene_download_is_empty(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="pixabay",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )
        subtitles = [(1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通")]

        def fail_llm(*args, **kwargs):
            raise AssertionError("Markdown task called an LLM")

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(tm.llm, "generate_script", side_effect=fail_llm),
                patch.object(tm.llm, "generate_terms", side_effect=fail_llm),
                patch.object(tm.llm, "generate_emphasis_terms", side_effect=fail_llm),
                patch.object(tm.llm, "generate_social_metadata", side_effect=fail_llm),
                patch.object(
                    tm,
                    "generate_audio",
                    return_value=("audio.wav", 2.0, object()),
                ),
                patch.object(tm.voice, "get_audio_duration", return_value=2.0),
                patch.object(tm, "generate_subtitle", return_value="alignment.srt"),
                patch.object(tm.subtitle, "file_to_subtitles", return_value=subtitles),
                patch.object(tm.material, "download_scene_materials", return_value=[]),
                patch.object(tm, "get_video_materials") as legacy_materials,
                patch.object(tm, "generate_final_videos") as final,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                result = tm.start("markdown-empty-scenes", params)

        self.assertIsNone(result)
        legacy_materials.assert_not_called()
        final.assert_not_called()
        update_task.assert_any_call(
            "markdown-empty-scenes", state=tm.const.TASK_STATE_FAILED
        )

    def test_markdown_task_marks_scene_download_errors_as_failed(self):
        params = VideoParams(
            video_subject="婚姻二字",
            video_source="pixabay",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻需要沟通。",
                        "emphasis_terms": [],
                        "material_search_terms": ["wedding couple"],
                    }
                ],
            },
        )
        subtitles = [(1, "00:00:00,000 --> 00:00:02,000", "婚姻需要沟通")]

        with tempfile.TemporaryDirectory() as task_dir:
            with (
                patch.object(tm.utils, "task_dir", return_value=task_dir),
                patch.object(
                    tm,
                    "generate_audio",
                    return_value=("audio.wav", 2.0, object()),
                ),
                patch.object(tm.voice, "get_audio_duration", return_value=2.0),
                patch.object(tm, "generate_subtitle", return_value="alignment.srt"),
                patch.object(tm.subtitle, "file_to_subtitles", return_value=subtitles),
                patch.object(
                    tm.material,
                    "download_scene_materials",
                    side_effect=tm.material.SceneMaterialError("download failed"),
                ),
                patch.object(tm, "get_video_materials") as legacy_materials,
                patch.object(tm, "generate_final_videos") as final,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                result = tm.start("markdown-download-error", params)

        self.assertIsNone(result)
        legacy_materials.assert_not_called()
        final.assert_not_called()
        update_task.assert_any_call(
            "markdown-download-error", state=tm.const.TASK_STATE_FAILED
        )

    def test_generate_terms_uses_script_order_mode_when_enabled(self):
        """
        默认模式不受影响；只有用户显式开启素材按文案顺序匹配时，任务层才
        要求 LLM 生成有序关键词，并适当增加关键词数量以覆盖更多脚本片段。
        """
        params = VideoParams(
            video_subject="城市通勤",
            video_script="",
            match_materials_to_script=True,
        )

        with patch.object(tm.llm, "generate_terms", return_value=["city", "train"]) as generate:
            result = tm.generate_terms("task-id", params, "先城市，再地铁")

        self.assertEqual(result, ["city", "train"])
        generate.assert_called_once_with(
            video_subject="城市通勤",
            video_script="先城市，再地铁",
            amount=8,
            match_script_order=True,
        )

    def test_start_stops_before_materials_when_term_provider_fails(self):
        """
        关键词 Provider 失败后，任务必须立即结束，不能继续生成音频或下载素材。

        这里从任务入口覆盖完整的错误传播路径，避免未来只修服务层返回类型，
        却又在任务编排层把空列表转换成其它真值后继续执行外部请求。
        """
        params = VideoParams(
            video_subject="startup story",
            video_script="A short startup story.",
        )

        with (
            patch.object(
                tm.llm,
                "_generate_response",
                return_value="Error: invalid API key",
            ),
            patch.object(tm, "generate_audio") as generate_audio,
            patch.object(tm, "get_video_materials") as get_video_materials,
            patch.object(tm.sm.state, "update_task") as update_task,
        ):
            result = tm.start("term-provider-error", params)

        self.assertIsNone(result)
        generate_audio.assert_not_called()
        get_video_materials.assert_not_called()
        update_task.assert_any_call(
            "term-provider-error",
            state=tm.const.TASK_STATE_FAILED,
        )
    
    def test_generate_audio_uses_custom_file_inside_task_directory(self):
        task_id = "test-custom-audio-safe"
        task_dir = utils.task_dir(task_id)
        custom_audio_file = os.path.join(task_dir, "custom-audio.mp3")
        with open(custom_audio_file, "wb") as audio:
            audio.write(b"fake audio")

        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=custom_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.voice, "get_audio_duration", return_value=7),
            ):
                audio_file, audio_duration, sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, os.path.realpath(custom_audio_file))
        self.assertEqual(audio_duration, 7)
        self.assertIsNone(sub_maker)
        tts.assert_not_called()

    def test_generate_audio_accepts_server_side_custom_file(self):
        task_id = "test-custom-audio-server-side"
        task_dir = utils.task_dir(task_id)

        with tempfile.NamedTemporaryFile(suffix=".mp3") as server_audio:
            server_audio.write(b"fake audio")
            server_audio.flush()
            params = VideoParams(
                video_subject="custom audio",
                video_script="",
                custom_audio_file=server_audio.name,
                voice_name="test-voice",
            )

            try:
                with (
                    patch.object(tm.voice, "tts") as tts,
                    patch.object(tm.voice, "get_audio_duration", return_value=6),
                ):
                    audio_file, audio_duration, result_sub_maker = tm.generate_audio(
                        task_id, params, "script"
                    )
            finally:
                shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, os.path.realpath(server_audio.name))
        self.assertEqual(audio_duration, 6)
        self.assertIsNone(result_sub_maker)
        tts.assert_not_called()

    def test_generate_audio_rejects_missing_custom_file_without_tts(self):
        task_id = "test-custom-audio-missing"
        task_dir = utils.task_dir(task_id)
        missing_audio_file = os.path.join(task_dir, "missing.mp3")
        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=missing_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                audio_file, audio_duration, result_sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertIsNone(audio_file)
        self.assertIsNone(audio_duration)
        self.assertIsNone(result_sub_maker)
        tts.assert_not_called()
        update_task.assert_called_with(task_id, state=tm.const.TASK_STATE_FAILED)

    def test_generate_audio_uses_local_voice_pipeline(self):
        task_id = "test-local-voice-audio"
        task_dir = utils.task_dir(task_id)
        params = VideoParams(
            video_subject="local voice",
            video_script="你好，世界。",
            voice_name="local:default",
        )
        result = SimpleNamespace(
            audio_file=Path(task_dir) / "audio.wav",
            duration=1.25,
        )
        try:
            with (
                patch.object(tm.config, "local_voice", {"enabled": True}),
                patch.object(tm.local_voice_service, "settings_from_config") as settings,
                patch.object(tm.local_voice_service, "LocalVoiceService") as service,
                patch.object(tm.voice, "tts") as tts,
            ):
                service.return_value.synthesize.return_value = result
                audio_file, audio_duration, sub_maker = tm.generate_audio(
                    task_id, params, "你好，世界。"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, str(result.audio_file))
        self.assertEqual(audio_duration, 2)
        self.assertIsNone(sub_maker)
        settings.assert_called_once()
        service.return_value.synthesize.assert_called_once_with(
            task_id,
            task_dir,
            "你好，世界。",
            "local:default",
            progress_callback=unittest.mock.ANY,
        )
        tts.assert_not_called()

    def test_disabled_local_voice_does_not_fall_back_to_remote_tts(self):
        task_id = "test-disabled-local-voice"
        params = VideoParams(
            video_subject="local voice",
            video_script="你好。",
            voice_name="local:default",
        )
        with (
            patch.object(tm.config, "local_voice", {"enabled": False}),
            patch.object(tm.voice, "tts") as tts,
            patch.object(tm.sm.state, "update_task") as update_task,
        ):
            result = tm.generate_audio(task_id, params, "你好。")

        self.assertEqual(result, (None, None, None))
        tts.assert_not_called()
        update_task.assert_called_with(task_id, state=tm.const.TASK_STATE_FAILED)

    def test_generate_subtitle_uses_qwen_for_local_voice_audio(self):
        task_id = "test-local-voice-subtitle"
        task_dir = utils.task_dir(task_id)
        audio_file = Path(task_dir) / "audio.wav"
        manifest_file = Path(task_dir) / "local_voice_manifest.json"
        audio_file.parent.mkdir(parents=True, exist_ok=True)
        audio_file.write_bytes(b"audio")
        manifest_file.write_text("{}", encoding="utf-8")
        params = VideoParams(
            video_subject="local voice",
            video_script="你好。",
            subtitle_enabled=True,
            video_language="Chinese",
        )
        subtitle_path = Path(task_dir) / "subtitle.srt"

        def fake_align(*args, **kwargs):
            subtitle_path.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n你好。\n\n",
                encoding="utf-8",
            )
            return subtitle_path

        try:
            with (
                patch.object(
                    tm.config,
                    "local_voice",
                    {"enabled": True, "subtitle_provider": "qwen_forced_aligner"},
                ),
                patch.object(
                    tm.local_voice_service,
                    "LocalVoiceService",
                ) as service,
            ):
                service.return_value.align_subtitle.side_effect = fake_align
                result = tm.generate_subtitle(
                    task_id, params, "你好。", None, str(audio_file)
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(result, str(subtitle_path))
        service.return_value.align_subtitle.assert_called_once()

    def test_local_subtitle_falls_back_to_whisper_when_alignment_fails(self):
        task_id = "test-local-voice-subtitle-fallback"
        task_dir = utils.task_dir(task_id)
        audio_file = Path(task_dir) / "audio.wav"
        audio_file.parent.mkdir(parents=True, exist_ok=True)
        audio_file.write_bytes(b"audio")
        (Path(task_dir) / "local_voice_manifest.json").write_text(
            "{}", encoding="utf-8"
        )
        params = VideoParams(
            video_subject="local voice",
            video_script="你好。",
            subtitle_enabled=True,
        )
        subtitle_path = Path(task_dir) / "subtitle.srt"

        def fake_whisper_create(audio_file, subtitle_file):
            Path(subtitle_file).write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n你好。\n\n",
                encoding="utf-8",
            )

        try:
            with (
                patch.object(
                    tm.config,
                    "local_voice",
                    {"enabled": True, "subtitle_fallback": "whisper"},
                ),
                patch.object(
                    tm.local_voice_service,
                    "LocalVoiceService",
                ) as service,
                patch.object(tm.subtitle, "create", side_effect=fake_whisper_create) as create,
                patch.object(tm.subtitle, "correct") as correct,
            ):
                service.return_value.align_subtitle.side_effect = (
                    tm.local_voice_service.LocalVoiceError("alignment failed")
                )
                result = tm.generate_subtitle(
                    task_id, params, "你好。", None, str(audio_file)
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(result, str(subtitle_path))
        create.assert_called_once_with(audio_file=str(audio_file), subtitle_file=str(subtitle_path))
        correct.assert_called_once_with(
            subtitle_file=str(subtitle_path), video_script="你好。"
        )

    def test_generate_subtitle_uses_whisper_for_custom_audio_without_sub_maker(self):
        """
        自定义音频不会经过 TTS，所以没有 sub_maker。
        Whisper 可以直接从音频文件转写，此时不能被 sub_maker 为空的保护逻辑提前跳过。
        """
        task_id = "test-custom-audio-whisper-subtitle"
        task_dir = utils.task_dir(task_id)
        audio_file = os.path.join(task_dir, "custom-audio.mp3")
        Path(audio_file).write_bytes(b"fake audio")
        params = VideoParams(
            video_subject="custom audio",
            video_script="Hello world.",
            subtitle_enabled=True,
        )

        def fake_whisper_create(audio_file, subtitle_file):
            Path(subtitle_file).write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHello world.\n\n",
                encoding="utf-8",
            )

        try:
            with (
                patch.object(
                    tm.config,
                    "app",
                    dict(tm.config.app, subtitle_provider="whisper"),
                ),
                patch.object(
                    tm.subtitle, "create", side_effect=fake_whisper_create
                ) as create,
                patch.object(tm.subtitle, "correct") as correct,
            ):
                subtitle_path = tm.generate_subtitle(
                    task_id=task_id,
                    params=params,
                    video_script="Hello world.",
                    sub_maker=None,
                    audio_file=audio_file,
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertTrue(subtitle_path.endswith("subtitle.srt"))
        create.assert_called_once_with(audio_file=audio_file, subtitle_file=subtitle_path)
        correct.assert_called_once_with(
            subtitle_file=subtitle_path, video_script="Hello world."
        )

    def test_generate_subtitle_skips_edge_provider_without_sub_maker(self):
        """
        Edge 字幕依赖 TTS 返回的 sub_maker 时间轴。
        自定义音频缺少该对象时应继续跳过，避免产生不可信的字幕时间轴。
        """
        task_id = "test-custom-audio-edge-no-submaker"
        task_dir = utils.task_dir(task_id)
        audio_file = os.path.join(task_dir, "custom-audio.mp3")
        Path(audio_file).write_bytes(b"fake audio")
        params = VideoParams(
            video_subject="custom audio",
            video_script="Hello world.",
            subtitle_enabled=True,
        )

        try:
            with (
                patch.object(
                    tm.config,
                    "app",
                    dict(tm.config.app, subtitle_provider="edge"),
                ),
                patch.object(tm.voice, "create_subtitle") as create_subtitle,
                patch.object(tm.subtitle, "create") as whisper_create,
            ):
                subtitle_path = tm.generate_subtitle(
                    task_id=task_id,
                    params=params,
                    video_script="Hello world.",
                    sub_maker=None,
                    audio_file=audio_file,
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(subtitle_path, "")
        create_subtitle.assert_not_called()
        whisper_create.assert_not_called()

    def test_start_returns_each_intermediate_result(self):
        """
        API 的 script、terms、audio、subtitle 和 materials 模式共用同一条任务
        流水线。每个提前停止点都要返回对应产物，同时不能误执行后续阶段。
        """
        expected_results = {
            "script": {"script": "generated script"},
            "terms": {
                "script": "generated script",
                "terms": ["coffee", "morning"],
            },
            "audio": {"audio_file": "audio.mp3", "audio_duration": 5},
            "subtitle": {"subtitle_path": "subtitle.srt"},
            "materials": {"materials": ["clip.mp4"]},
        }

        for stop_at, expected in expected_results.items():
            with self.subTest(stop_at=stop_at):
                params = VideoParams(video_subject="Coffee")
                with (
                    patch.object(tm, "generate_script", return_value="generated script"),
                    patch.object(
                        tm,
                        "generate_terms",
                        return_value=["coffee", "morning"],
                    ),
                    patch.object(tm, "save_script_data"),
                    patch.object(
                        tm,
                        "generate_audio",
                        return_value=("audio.mp3", 5, object()),
                    ),
                    patch.object(
                        tm,
                        "generate_subtitle",
                        return_value="subtitle.srt",
                    ),
                    patch.object(
                        tm,
                        "get_video_materials",
                        return_value=["clip.mp4"],
                    ),
                    patch.object(tm, "generate_final_videos") as generate_final,
                    patch.object(tm.sm.state, "update_task"),
                ):
                    result = tm.start(
                        f"intermediate-{stop_at}", params, stop_at=stop_at
                    )

                self.assertEqual(result, expected)
                generate_final.assert_not_called()

    def test_start_completes_video_without_cross_posting(self):
        """
        完整任务在自动发布未配置时仍应稳定完成，并把所有中间产物写入最终
        状态。这里还覆盖 API 可能传入字符串拼接模式的兼容转换。
        """
        params = VideoParams(video_subject="Coffee")
        params.video_concat_mode = "sequential"

        with (
            patch.object(tm, "generate_script", return_value="generated script"),
            patch.object(tm, "generate_terms", return_value=["coffee"]),
            patch.object(tm, "save_script_data"),
            patch.object(
                tm,
                "generate_audio",
                return_value=("audio.mp3", 5, object()),
            ),
            patch.object(tm, "generate_subtitle", return_value="subtitle.srt"),
            patch.object(
                tm,
                "get_video_materials",
                return_value=["clip.mp4"],
            ),
            patch.object(
                tm,
                "generate_final_videos",
                return_value=(["final.mp4"], ["combined.mp4"]),
            ),
            patch.object(
                tm.upload_post.upload_post_service,
                "is_configured",
                return_value=False,
            ),
            patch.object(tm.upload_post, "cross_post_video") as cross_post,
            patch.object(tm.sm.state, "update_task") as update_task,
        ):
            result = tm.start("complete-video", params)

        self.assertEqual(result["videos"], ["final.mp4"])
        self.assertEqual(result["combined_videos"], ["combined.mp4"])
        self.assertEqual(result["cross_post_results"], None)
        self.assertEqual(params.video_concat_mode, tm.VideoConcatMode.sequential)
        cross_post.assert_not_called()
        update_task.assert_called_with(
            "complete-video",
            state=tm.const.TASK_STATE_COMPLETE,
            progress=100,
            stage="complete",
            stage_progress=100,
            detail="视频生成完成",
            **result,
        )

    def test_start_marks_pipeline_failures(self):
        """
        音频、素材和最终视频任一关键产物缺失时都必须进入失败状态，不能把
        不完整任务误报为完成。三个场景复用相同 mock，仅替换故障阶段。
        """
        failure_cases = {
            "audio": (
                (None, None, None),
                ["clip.mp4"],
                (["final.mp4"], ["combined.mp4"]),
            ),
            "materials": (
                ("audio.mp3", 5, object()),
                None,
                (["final.mp4"], ["combined.mp4"]),
            ),
            "video": (("audio.mp3", 5, object()), ["clip.mp4"], ([], [])),
        }

        for stage, failure_results in failure_cases.items():
            with self.subTest(stage=stage):
                audio_result, materials_result, videos_result = failure_results
                params = VideoParams(video_subject="Coffee")
                with (
                    patch.object(tm, "generate_script", return_value="generated script"),
                    patch.object(tm, "generate_terms", return_value=["coffee"]),
                    patch.object(tm, "save_script_data"),
                    patch.object(tm, "generate_audio", return_value=audio_result),
                    patch.object(tm, "generate_subtitle", return_value="subtitle.srt"),
                    patch.object(
                        tm,
                        "get_video_materials",
                        return_value=materials_result,
                    ),
                    patch.object(
                        tm,
                        "generate_final_videos",
                        return_value=videos_result,
                    ),
                    patch.object(tm.sm.state, "update_task") as update_task,
                ):
                    result = tm.start(f"failed-{stage}", params)

                self.assertIsNone(result)
                update_task.assert_any_call(
                    f"failed-{stage}", state=tm.const.TASK_STATE_FAILED
                )

    def test_start_generates_youtube_metadata_for_each_cross_post(self):
        """
        自动发布到 YouTube 时只生成一次元数据，但要把同一份字段传给每个
        成片，并在任务结果中保留每次上传成功或失败的独立结果。
        """
        params = VideoParams(
            video_subject="Coffee",
            video_language="en",
        )
        metadata = {
            "title": "Morning Coffee",
            "caption": "A better morning.",
            "hashtags": ["coffee", "shorts"],
        }
        service = tm.upload_post.upload_post_service

        with (
            patch.object(tm, "generate_script", return_value="generated script"),
            patch.object(tm, "generate_terms", return_value=["coffee"]),
            patch.object(tm, "save_script_data"),
            patch.object(
                tm,
                "generate_audio",
                return_value=("audio.mp3", 5, object()),
            ),
            patch.object(tm, "generate_subtitle", return_value="subtitle.srt"),
            patch.object(
                tm,
                "get_video_materials",
                return_value=["clip.mp4"],
            ),
            patch.object(
                tm,
                "generate_final_videos",
                return_value=(
                    ["final-1.mp4", "final-2.mp4"],
                    ["combined-1.mp4", "combined-2.mp4"],
                ),
            ),
            patch.object(service, "is_configured", return_value=True),
            patch.object(service, "auto_upload", True),
            patch.object(service, "platforms", ["youtube"]),
            patch.object(service, "youtube_privacy_status", "unlisted"),
            patch.object(
                tm.llm,
                "generate_social_metadata",
                return_value=metadata,
            ) as generate_metadata,
            patch.object(
                tm.upload_post,
                "cross_post_video",
                side_effect=[
                    {"success": True},
                    {"success": False, "error": "upload failed"},
                ],
            ) as cross_post,
            patch.object(tm.sm.state, "update_task"),
        ):
            result = tm.start("youtube-cross-post", params)

        generate_metadata.assert_called_once_with(
            video_subject="Coffee",
            video_script="generated script",
            language="en",
            platform="youtube_shorts",
        )
        expected_extra = {
            "youtube_title": "Morning Coffee",
            "youtube_description": "A better morning.",
            "tags": ["coffee", "shorts"],
            "privacyStatus": "unlisted",
            "containsSyntheticMedia": True,
        }
        self.assertEqual(cross_post.call_count, 2)
        for call in cross_post.call_args_list:
            self.assertEqual(call.kwargs["youtube_extra"], expected_extra)
        self.assertEqual(
            result["cross_post_results"],
            [
                {"success": True},
                {"success": False, "error": "upload failed"},
            ],
        )

    @unittest.skipUnless(
        RUN_INTEGRATION_TESTS,
        "MPT_RUN_INTEGRATION_TESTS not set",
    )
    def test_task_local_materials(self):
        task_id = "00000000-0000-0000-0000-000000000000"
        video_materials=[]
        for i in range(1, 4):
            video_materials.append(MaterialInfo(
                provider="local",
                url=os.path.join(resources_dir, f"{i}.png"),
                duration=0
            ))

        params = VideoParams(
            video_subject="金钱的作用",
            video_script="金钱不仅是交换媒介，更是社会资源的分配工具。它能满足基本生存需求，如食物和住房，也能提供教育、医疗等提升生活品质的机会。拥有足够的金钱意味着更多选择权，比如职业自由或创业可能。但金钱的作用也有边界，它无法直接购买幸福、健康或真诚的人际关系。过度追逐财富可能导致价值观扭曲，忽视精神层面的需求。理想的状态是理性看待金钱，将其作为实现目标的工具而非终极目的。",
            video_terms="money importance, wealth and society, financial freedom, money and happiness, role of money",
            video_aspect="9:16",
            video_concat_mode="random",
            video_transition_mode="None",
            video_clip_duration=3,
            video_count=1,
            video_source="local",
            video_materials=video_materials,
            video_language="",
            voice_name="zh-CN-XiaoxiaoNeural-Female",
            voice_volume=1.0,
            voice_rate=1.0,
            bgm_type="random",
            bgm_file="",
            bgm_volume=0.2,
            subtitle_enabled=True,
            subtitle_position="bottom",
            custom_position=70.0,
            font_name="MicrosoftYaHeiBold.ttc",
            text_fore_color="#FFFFFF",
            text_background_color=True,
            font_size=60,
            stroke_color="#000000",
            stroke_width=1.5,
            n_threads=2,
            paragraph_number=1
        )
        result = tm.start(task_id=task_id, params=params)
        print(result)
    

if __name__ == "__main__":
    unittest.main()
