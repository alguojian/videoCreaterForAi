import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.models.schema import VideoAspect
from app.services import material
from app.services.script_document import TimedScriptScene


class TestSceneMaterialAcquisition(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)
        self.original_proxy_config = dict(config.proxy)

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)
        config.proxy.clear()
        config.proxy.update(self.original_proxy_config)

    def test_download_scene_materials_tries_fallbacks_and_covers_each_scene(self):
        scenes = [
            TimedScriptScene(
                1, 1, 2, ("first query", "fallback query"), 0.0, 6.0
            ),
            TimedScriptScene(2, 3, 3, ("second scene",), 6.0, 9.0),
        ]
        result_map = {
            "first query": [],
            "fallback query": [
                material.MaterialInfo(
                    provider="pixabay", url="https://x/a.mp4", duration=4
                ),
                material.MaterialInfo(
                    provider="pixabay", url="https://x/b.mp4", duration=4
                ),
            ],
            "second scene": [
                material.MaterialInfo(
                    provider="pixabay", url="https://x/c.mp4", duration=5
                )
            ],
        }

        def fake_search(search_term, minimum_duration, video_aspect):
            return result_map[search_term]

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.dict(config.app, {"material_directory": temp_dir}),
                patch.object(
                    material, "search_videos_pixabay", side_effect=fake_search
                ) as search,
                patch.object(
                    material,
                    "save_video",
                    side_effect=lambda video_url, save_dir: str(
                        Path(save_dir) / Path(video_url).name
                    ),
                ),
            ):
                plans = material.download_scene_materials(
                    task_id="scene-task",
                    scenes=scenes,
                    source="pixabay",
                    video_aspect=VideoAspect.landscape,
                    max_clip_duration=5,
                )

        self.assertEqual(
            [call.kwargs["search_term"] for call in search.call_args_list],
            ["first query", "fallback query", "second scene"],
        )
        self.assertEqual([plan.scene_index for plan in plans], [1, 2])
        self.assertEqual([len(plan.video_paths) for plan in plans], [2, 1])
        self.assertEqual(plans[0].required_duration, 6.0)
        self.assertIsInstance(plans[0].search_terms, tuple)
        self.assertIsInstance(plans[0].video_paths, tuple)

    def test_download_scene_materials_reports_scene_rows_and_all_queries(self):
        scenes = [
            TimedScriptScene(
                3, 4, 6, ("primary phrase", "backup phrase"), 10.0, 14.0
            )
        ]

        with patch.object(material, "search_videos_pixabay", return_value=[]):
            with self.assertRaises(material.SceneMaterialError) as raised:
                material.download_scene_materials(
                    task_id="scene-task",
                    scenes=scenes,
                    source="pixabay",
                    video_aspect=VideoAspect.landscape,
                    max_clip_duration=5,
                )

        message = str(raised.exception)
        self.assertIn("场景 3", message)
        self.assertIn("第 4～6 行", message)
        self.assertIn("primary phrase", message)
        self.assertIn("backup phrase", message)

    def test_pixabay_search_log_never_contains_the_api_key(self):
        secret = "secret-pixabay-key"
        config.app["pixabay_api_keys"] = [secret]
        config.proxy.clear()
        fake_response = SimpleNamespace(json=lambda: {"hits": []})

        with (
            patch.object(material.requests, "get", return_value=fake_response),
            patch.object(material.logger, "info") as info,
        ):
            material.search_videos_pixabay(
                "wedding couple",
                minimum_duration=1,
                video_aspect=VideoAspect.landscape,
            )

        log_output = "\n".join(str(call) for call in info.call_args_list)
        self.assertNotIn(secret, log_output)
        self.assertIn("wedding couple", log_output)

    def test_pixabay_exception_log_never_contains_key_or_request_url(self):
        secret = "unit-test-pixabay-secret"
        request_url = f"https://pixabay.test/videos?q=wedding&key={secret}"
        config.app["pixabay_api_keys"] = [secret]
        config.proxy.clear()

        with (
            patch.object(
                material.requests,
                "get",
                side_effect=requests.ConnectionError(request_url),
            ),
            patch.object(material.logger, "info") as info,
            patch.object(material.logger, "error") as error,
        ):
            results = material.search_videos_pixabay(
                "wedding couple",
                minimum_duration=1,
                video_aspect=VideoAspect.landscape,
            )

        log_output = "\n".join(
            str(call) for call in info.call_args_list + error.call_args_list
        )
        self.assertEqual(results, [])
        self.assertNotIn(secret, log_output)
        self.assertNotIn(request_url, log_output)
        self.assertIn("ConnectionError", log_output)

    def test_pixabay_invalid_response_log_does_not_echo_secret_values(self):
        secret = "unit-test-pixabay-secret"
        config.app["pixabay_api_keys"] = [secret]
        config.proxy.clear()
        fake_response = SimpleNamespace(
            json=lambda: {"error": f"request rejected for key={secret}"}
        )

        with (
            patch.object(material.requests, "get", return_value=fake_response),
            patch.object(material.logger, "info") as info,
            patch.object(material.logger, "error") as error,
        ):
            results = material.search_videos_pixabay(
                "wedding couple",
                minimum_duration=1,
                video_aspect=VideoAspect.landscape,
            )

        log_output = "\n".join(
            str(call) for call in info.call_args_list + error.call_args_list
        )
        self.assertEqual(results, [])
        self.assertNotIn(secret, log_output)

    def test_scene_materials_reject_unsupported_sources(self):
        for source in ("local", "unknown"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    material.SceneMaterialError, f"不支持素材源：{source}"
                ):
                    material.download_scene_materials(
                        task_id="scene-task",
                        scenes=[],
                        source=source,
                        video_aspect=VideoAspect.landscape,
                        max_clip_duration=5,
                    )

    def test_scene_materials_validate_scene_and_clip_duration(self):
        valid = TimedScriptScene(1, 1, 1, ("valid query",), 0.0, 3.0)
        cases = [
            (
                [TimedScriptScene(1, 1, 1, ("valid query",), 3.0, 3.0)],
                5,
                "结束时间必须晚于开始时间",
            ),
            (
                [TimedScriptScene(1, 1, 1, (), 0.0, 3.0)],
                5,
                "搜索词不能为空",
            ),
            ([valid], 0, "最大片段时长必须是有限正数"),
        ]

        for scenes, max_clip_duration, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(material.SceneMaterialError, expected):
                    material.download_scene_materials(
                        task_id="scene-task",
                        scenes=scenes,
                        source="pixabay",
                        video_aspect=VideoAspect.landscape,
                        max_clip_duration=max_clip_duration,
                    )

    def test_scene_materials_do_not_reuse_a_url_across_scenes(self):
        scenes = [
            TimedScriptScene(1, 1, 1, ("scene one",), 0.0, 3.0),
            TimedScriptScene(2, 2, 2, ("scene two",), 3.0, 6.0),
        ]
        shared = material.MaterialInfo(
            provider="pixabay", url="https://x/shared.mp4", duration=4
        )
        unique = material.MaterialInfo(
            provider="pixabay", url="https://x/unique.mp4", duration=4
        )
        result_map = {"scene one": [shared], "scene two": [shared, unique]}

        with (
            patch.object(
                material,
                "search_videos_pixabay",
                side_effect=lambda search_term, **_: result_map[search_term],
            ),
            patch.object(
                material,
                "save_video",
                side_effect=lambda video_url, save_dir: f"saved/{Path(video_url).name}",
            ) as save,
        ):
            plans = material.download_scene_materials(
                task_id="scene-task",
                scenes=scenes,
                source="pixabay",
                video_aspect=VideoAspect.landscape,
                max_clip_duration=5,
            )

        self.assertEqual(
            [call.args[0] for call in save.call_args_list],
            ["https://x/shared.mp4", "https://x/unique.mp4"],
        )
        self.assertEqual(
            [plan.video_paths for plan in plans],
            [("saved/shared.mp4",), ("saved/unique.mp4",)],
        )

    def test_scene_materials_retry_same_url_after_failed_save(self):
        scene = TimedScriptScene(
            1, 1, 1, ("primary query", "fallback query"), 0.0, 3.0
        )
        item = material.MaterialInfo(
            provider="pixabay", url="https://x/retry.mp4", duration=4
        )

        with (
            patch.object(material, "search_videos_pixabay", return_value=[item]),
            patch.object(
                material,
                "save_video",
                side_effect=["", "saved/retry.mp4"],
            ) as save,
        ):
            plans = material.download_scene_materials(
                task_id="scene-task",
                scenes=[scene],
                source="pixabay",
                video_aspect=VideoAspect.landscape,
                max_clip_duration=5,
            )

        self.assertEqual(save.call_count, 2)
        self.assertEqual(plans[0].video_paths, ("saved/retry.mp4",))

    def test_scene_materials_deduplicate_signed_urls_by_cache_identity(self):
        scene = TimedScriptScene(1, 1, 1, ("signed query",), 0.0, 6.0)
        candidates = [
            material.MaterialInfo(
                provider="pixabay",
                url="https://x/same.mp4?signature=first",
                duration=4,
            ),
            material.MaterialInfo(
                provider="pixabay",
                url="https://x/same.mp4?signature=second",
                duration=4,
            ),
            material.MaterialInfo(
                provider="pixabay",
                url="https://x/distinct.mp4?signature=third",
                duration=4,
            ),
        ]

        with (
            patch.object(material, "search_videos_pixabay", return_value=candidates),
            patch.object(
                material,
                "save_video",
                side_effect=lambda video_url, save_dir: f"saved/{Path(video_url).name}",
            ) as save,
        ):
            plans = material.download_scene_materials(
                task_id="scene-task",
                scenes=[scene],
                source="pixabay",
                video_aspect=VideoAspect.landscape,
                max_clip_duration=5,
            )

        self.assertEqual(
            [call.args[0] for call in save.call_args_list],
            [
                "https://x/same.mp4?signature=first",
                "https://x/distinct.mp4?signature=third",
            ],
        )
        self.assertEqual(len(plans[0].video_paths), 2)

    def test_failed_signed_url_can_retry_same_cache_identity(self):
        scene = TimedScriptScene(
            1, 1, 1, ("primary query", "fallback query"), 0.0, 3.0
        )
        result_map = {
            "primary query": [
                material.MaterialInfo(
                    provider="pixabay",
                    url="https://x/retry.mp4?signature=expired",
                    duration=4,
                )
            ],
            "fallback query": [
                material.MaterialInfo(
                    provider="pixabay",
                    url="https://x/retry.mp4?signature=fresh",
                    duration=4,
                )
            ],
        }

        with (
            patch.object(
                material,
                "search_videos_pixabay",
                side_effect=lambda search_term, **_: result_map[search_term],
            ),
            patch.object(
                material,
                "save_video",
                side_effect=["", "saved/retry.mp4"],
            ) as save,
        ):
            plans = material.download_scene_materials(
                task_id="scene-task",
                scenes=[scene],
                source="pixabay",
                video_aspect=VideoAspect.landscape,
                max_clip_duration=5,
            )

        self.assertEqual(save.call_count, 2)
        self.assertEqual(plans[0].video_paths, ("saved/retry.mp4",))

    def test_invalid_duration_does_not_block_same_url_in_fallback_query(self):
        scene = TimedScriptScene(
            1, 1, 1, ("invalid duration", "valid fallback"), 0.0, 3.0
        )
        result_map = {
            "invalid duration": [
                material.MaterialInfo(
                    provider="pixabay", url="https://x/retry.mp4", duration=0
                )
            ],
            "valid fallback": [
                material.MaterialInfo(
                    provider="pixabay", url="https://x/retry.mp4", duration=4
                )
            ],
        }

        with (
            patch.object(
                material,
                "search_videos_pixabay",
                side_effect=lambda search_term, **_: result_map[search_term],
            ),
            patch.object(
                material, "save_video", return_value="saved/retry.mp4"
            ) as save,
        ):
            plans = material.download_scene_materials(
                task_id="scene-task",
                scenes=[scene],
                source="pixabay",
                video_aspect=VideoAspect.landscape,
                max_clip_duration=5,
            )

        save.assert_called_once_with("https://x/retry.mp4", "")
        self.assertEqual(plans[0].video_paths, ("saved/retry.mp4",))

    def test_non_finite_candidate_durations_are_rejected(self):
        scene = TimedScriptScene(1, 1, 1, ("query",), 0.0, 3.0)

        for duration in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(duration=duration):
                item = SimpleNamespace(
                    provider="pixabay",
                    url="https://x/non-finite.mp4",
                    duration=duration,
                )
                with (
                    patch.object(
                        material, "search_videos_pixabay", return_value=[item]
                    ),
                    patch.object(
                        material, "save_video", return_value="saved/non-finite.mp4"
                    ) as save,
                ):
                    with self.assertRaises(material.SceneMaterialError):
                        material.download_scene_materials(
                            task_id="scene-task",
                            scenes=[scene],
                            source="pixabay",
                            video_aspect=VideoAspect.landscape,
                            max_clip_duration=5,
                        )
                save.assert_not_called()

    def test_valid_float_candidate_after_non_finite_item_still_covers_scene(self):
        scene = TimedScriptScene(1, 1, 1, ("query",), 0.0, 3.5)
        candidates = [
            SimpleNamespace(
                provider="pixabay",
                url="https://x/non-finite.mp4",
                duration=float("nan"),
            ),
            SimpleNamespace(
                provider="pixabay", url="https://x/valid.mp4", duration=3.75
            ),
        ]

        with (
            patch.object(material, "search_videos_pixabay", return_value=candidates),
            patch.object(
                material, "save_video", return_value="saved/valid.mp4"
            ) as save,
        ):
            plans = material.download_scene_materials(
                task_id="scene-task",
                scenes=[scene],
                source="pixabay",
                video_aspect=VideoAspect.landscape,
                max_clip_duration=5,
            )

        save.assert_called_once_with("https://x/valid.mp4", "")
        self.assertEqual(plans[0].required_duration, 3.5)

    def test_scene_materials_reject_non_finite_scene_times_and_clip_duration(self):
        valid_item = material.MaterialInfo(
            provider="pixabay", url="https://x/valid.mp4", duration=4
        )
        cases = [
            (
                TimedScriptScene(1, 1, 1, ("query",), float("nan"), 3.0),
                5,
                "场景 1 时间必须是有限值",
            ),
            (
                TimedScriptScene(1, 1, 1, ("query",), 0.0, float("inf")),
                5,
                "场景 1 时间必须是有限值",
            ),
            (
                TimedScriptScene(1, 1, 1, ("query",), 0.0, 3.0),
                float("nan"),
                "最大片段时长必须是有限正数",
            ),
            (
                TimedScriptScene(1, 1, 1, ("query",), 0.0, 3.0),
                float("inf"),
                "最大片段时长必须是有限正数",
            ),
        ]

        for scene, max_clip_duration, expected in cases:
            with self.subTest(expected=expected, max_clip_duration=max_clip_duration):
                with patch.object(
                    material, "search_videos_pixabay", return_value=[valid_item]
                ):
                    with self.assertRaisesRegex(
                        material.SceneMaterialError, expected
                    ):
                        material.download_scene_materials(
                            task_id="scene-task",
                            scenes=[scene],
                            source="pixabay",
                            video_aspect=VideoAspect.landscape,
                            max_clip_duration=max_clip_duration,
                        )

    def test_scene_materials_only_count_positive_successfully_saved_items(self):
        scene = TimedScriptScene(1, 1, 1, ("query",), 0.0, 3.0)
        candidates = [
            material.MaterialInfo(
                provider="pixabay", url="https://x/zero.mp4", duration=0
            ),
            material.MaterialInfo(
                provider="pixabay", url="https://x/empty.mp4", duration=4
            ),
            material.MaterialInfo(
                provider="pixabay", url="https://x/error.mp4", duration=4
            ),
            material.MaterialInfo(
                provider="pixabay", url="https://x/good.mp4", duration=4
            ),
        ]

        def fake_save(video_url, save_dir):
            if video_url.endswith("empty.mp4"):
                return ""
            if video_url.endswith("error.mp4"):
                raise OSError("disk unavailable")
            return f"saved/{Path(video_url).name}"

        with (
            patch.object(material, "search_videos_pixabay", return_value=candidates),
            patch.object(material, "save_video", side_effect=fake_save) as save,
            patch.object(material.logger, "error") as error,
        ):
            plans = material.download_scene_materials(
                task_id="scene-task",
                scenes=[scene],
                source="pixabay",
                video_aspect=VideoAspect.landscape,
                max_clip_duration=5,
            )

        self.assertEqual(plans[0].video_paths, ("saved/good.mp4",))
        self.assertEqual(
            [call.args[0] for call in save.call_args_list],
            [
                "https://x/empty.mp4",
                "https://x/error.mp4",
                "https://x/good.mp4",
            ],
        )
        self.assertTrue(error.called)

    def test_scene_materials_propagate_search_configuration_errors(self):
        scene = TimedScriptScene(1, 1, 1, ("query",), 0.0, 3.0)

        with patch.object(
            material,
            "search_videos_pixabay",
            side_effect=ValueError("pixabay_api_keys is not set"),
        ):
            with self.assertRaisesRegex(ValueError, "pixabay_api_keys"):
                material.download_scene_materials(
                    task_id="scene-task",
                    scenes=[scene],
                    source="pixabay",
                    video_aspect=VideoAspect.landscape,
                    max_clip_duration=5,
                )

    def test_scene_material_directory_matches_existing_download_semantics(self):
        with patch.dict(config.app, {"material_directory": "task"}), patch.object(
            material.utils, "task_dir", return_value="task-specific"
        ) as task_dir:
            self.assertEqual(
                material._scene_material_directory("scene-task"), "task-specific"
            )
            task_dir.assert_called_once_with("scene-task")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(config.app, {"material_directory": temp_dir}):
                self.assertEqual(
                    material._scene_material_directory("scene-task"), temp_dir
                )

        with patch.dict(
            config.app, {"material_directory": "Z:/path/that/does/not/exist"}
        ):
            self.assertEqual(material._scene_material_directory("scene-task"), "")


class TestMaterialTlsVerification(unittest.TestCase):
    def setUp(self):
        self.original_app_config = dict(config.app)
        self.original_proxy_config = dict(config.proxy)

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)
        config.proxy.clear()
        config.proxy.update(self.original_proxy_config)

    def test_search_pexels_uses_tls_verification_by_default(self):
        """
        默认路径必须开启 TLS 校验，避免素材 API key 和返回的素材 URL
        在公共网络或不可信代理环境中被中间人攻击截获或篡改。
        """
        config.app["pexels_api_keys"] = ["pexels-key"]
        config.app.pop("tls_verify", None)
        config.proxy.clear()

        fake_response = SimpleNamespace(
            json=lambda: {
                "videos": [
                    {
                        "duration": 8,
                        "video_files": [
                            {
                                "width": 1080,
                                "height": 1920,
                                "link": "https://example.com/video.mp4",
                            }
                        ],
                    }
                ]
            }
        )

        with patch("app.services.material.requests.get", return_value=fake_response) as get:
            results = material.search_videos_pexels("cat", minimum_duration=1)

        self.assertEqual(len(results), 1)
        self.assertTrue(get.call_args.kwargs["verify"])

    def test_search_pixabay_allows_explicit_tls_disable_for_proxy(self):
        """
        少数企业代理会使用自签证书。该场景必须显式配置关闭 TLS 校验，
        不能再由代码硬编码默认关闭。
        """
        config.app["pixabay_api_keys"] = ["pixabay-key"]
        config.app["tls_verify"] = False
        config.proxy.clear()

        fake_response = SimpleNamespace(
            json=lambda: {
                "hits": [
                    {
                        "duration": 8,
                        "videos": {
                            "large": {
                                "width": 1920,
                                "height": 1080,
                                "url": "https://example.com/video.mp4",
                            }
                        },
                    }
                ]
            }
        )

        with patch("app.services.material.requests.get", return_value=fake_response) as get:
            results = material.search_videos_pixabay(
                "cat", minimum_duration=1, video_aspect=VideoAspect.landscape
            )

        self.assertEqual(len(results), 1)
        self.assertFalse(get.call_args.kwargs["verify"])

    def test_search_pixabay_filters_to_portrait_and_best_matching_ratio(self):
        config.app["pixabay_api_keys"] = ["pixabay-key"]
        fake_response = SimpleNamespace(
            json=lambda: {
                "hits": [
                    {
                        "duration": 8,
                        "videos": {
                            "large": {
                                "width": 1920,
                                "height": 1080,
                                "url": "https://example.com/landscape.mp4",
                            },
                            "medium": {
                                "width": 1080,
                                "height": 1920,
                                "url": "https://example.com/portrait.mp4",
                            },
                            "near": {
                                "width": 1080,
                                "height": 1600,
                                "url": "https://example.com/near-portrait.mp4",
                            },
                        },
                    }
                ]
            }
        )

        with patch("app.services.material.requests.get", return_value=fake_response):
            results = material.search_videos_pixabay(
                "wedding", minimum_duration=1, video_aspect=VideoAspect.portrait
            )

        self.assertEqual([item.url for item in results], ["https://example.com/portrait.mp4"])

    def test_search_pixabay_filters_to_landscape_for_landscape_output(self):
        config.app["pixabay_api_keys"] = ["pixabay-key"]
        fake_response = SimpleNamespace(
            json=lambda: {
                "hits": [
                    {
                        "duration": 8,
                        "videos": {
                            "portrait": {
                                "width": 2160,
                                "height": 3840,
                                "url": "https://example.com/portrait.mp4",
                            },
                            "landscape": {
                                "width": 1920,
                                "height": 1080,
                                "url": "https://example.com/landscape.mp4",
                            },
                        },
                    }
                ]
            }
        )

        with patch("app.services.material.requests.get", return_value=fake_response):
            results = material.search_videos_pixabay(
                "wedding", minimum_duration=1, video_aspect=VideoAspect.landscape
            )

        self.assertEqual([item.url for item in results], ["https://example.com/landscape.mp4"])

    def test_save_video_uses_tls_verification_by_default(self):
        config.app.pop("tls_verify", None)
        config.proxy.clear()

        fake_response = SimpleNamespace(content=b"fake-video")

        class FakeVideoFileClip:
            duration = 1
            fps = 24

            def __init__(self, path):
                self.path = path

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "app.services.material.requests.get", return_value=fake_response
            ) as get, patch("app.services.material.VideoFileClip", FakeVideoFileClip):
                video_path = material.save_video(
                    "https://example.com/video.mp4?token=abc", save_dir=temp_dir
                )

            self.assertTrue(os.path.exists(video_path))
            self.assertTrue(get.call_args.kwargs["verify"])

    def test_download_videos_accepts_plain_string_concat_mode(self):
        """
        download_videos 可能被服务层或测试直接传入字符串模式，而不是
        VideoConcatMode 枚举。这里用空搜索词避免真实网络请求，只验证
        字符串 "random" 不会再因为访问 `.value` 抛 AttributeError。
        """
        result = material.download_videos(
            task_id="string-concat-mode",
            search_terms=[],
            video_concat_mode="random",
        )

        self.assertEqual(result, [])

    def test_download_videos_can_round_robin_terms_in_script_order(self):
        """
        开启按文案顺序匹配素材后，不能让第一个关键词的多个候选先把
        音频时长填满。这里模拟两个关键词各有多个候选，验证下载顺序是
        term1-第1个、term2-第1个、term1-第2个，贴近脚本叙事顺序。
        """
        search_results = {
            "opening city": [
                material.MaterialInfo(provider="pexels", url="https://v.example/a1.mp4", duration=3),
                material.MaterialInfo(provider="pexels", url="https://v.example/a2.mp4", duration=3),
            ],
            "middle office": [
                material.MaterialInfo(provider="pexels", url="https://v.example/b1.mp4", duration=3),
                material.MaterialInfo(provider="pexels", url="https://v.example/b2.mp4", duration=3),
            ],
        }
        downloaded_urls = []

        def fake_search(search_term, minimum_duration, video_aspect):
            return search_results[search_term]

        def fake_save_video(video_url, save_dir=""):
            downloaded_urls.append(video_url)
            return f"/tmp/{video_url.rsplit('/', 1)[-1]}"

        with (
            patch.dict(config.app, {"material_directory": ""}),
            patch.object(material, "search_videos_pexels", side_effect=fake_search),
            patch.object(material, "save_video", side_effect=fake_save_video),
        ):
            result = material.download_videos(
                task_id="ordered-materials",
                search_terms=["opening city", "middle office"],
                source="pexels",
                audio_duration=7,
                max_clip_duration=3,
                match_script_order=True,
            )

        self.assertEqual(
            downloaded_urls,
            [
                "https://v.example/a1.mp4",
                "https://v.example/b1.mp4",
                "https://v.example/a2.mp4",
            ],
        )
        self.assertEqual(result, ["/tmp/a1.mp4", "/tmp/b1.mp4", "/tmp/a2.mp4"])


class TestCoverrProvider(unittest.TestCase):
    """
    Coverr 视频素材源(spec: 2026-06-09-coverr-video-provider-design.md)。
    全部用 unittest.mock 替换 requests，确保 CI 不依赖真实网络和真实 API key。
    """

    def setUp(self):
        self.original_app_config = dict(config.app)
        self.original_proxy_config = dict(config.proxy)

    def tearDown(self):
        config.app.clear()
        config.app.update(self.original_app_config)
        config.proxy.clear()
        config.proxy.update(self.original_proxy_config)

    # ---------------- Tests for search_videos_coverr ----------------

    def test_search_coverr_uses_mp4_download_url(self):
        """
        search_videos_coverr 应把每个 hit 转成 MaterialInfo，并把 urls.mp4_download
        直接作为 MaterialInfo.url。
        按 Coverr 官方文档 (api.coverr.co/docs/videos/#download-a-video),
        GET mp4_download 本身就被 Coverr 计入下载统计,无需额外 PATCH ping。
        同时验证 Authorization header 使用 Bearer scheme。
        """
        config.app["coverr_api_keys"] = ["coverr-key"]
        config.app.pop("tls_verify", None)
        config.proxy.clear()

        fake_response = SimpleNamespace(
            json=lambda: {
                "page": 0,
                "pages": 50,
                "page_size": 20,
                "total": 1,
                "hits": [
                    {
                        "id": "S1YbPl1NfI",
                        "duration": 11.625,
                        "aspect_ratio": "16:9",
                        "urls": {
                            "mp4": "https://storage.coverr.co/videos/abc?token=xyz",
                            "mp4_preview": "https://storage.coverr.co/videos/abc/preview?token=xyz",
                            "mp4_download": "https://storage.coverr.co/videos/abc/download?token=xyz",
                        },
                    }
                ],
            }
        )

        with patch(
            "app.services.material.requests.get", return_value=fake_response
        ) as get:
            results = material.search_videos_coverr("nature", minimum_duration=5)

        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item.provider, "coverr")
        self.assertEqual(item.duration, 11)
        # url 字段就是 mp4_download URL,不再做 coverr://id|url 编码
        self.assertEqual(
            item.url, "https://storage.coverr.co/videos/abc/download?token=xyz"
        )
        # Bearer auth + TLS verify on by default
        self.assertEqual(
            get.call_args.kwargs["headers"]["Authorization"], "Bearer coverr-key"
        )
        self.assertTrue(get.call_args.kwargs["verify"])

    def test_search_coverr_uses_tls_verification_by_default(self):
        """与 pexels/pixabay 一致:未显式配置时 TLS 校验默认开启。"""
        config.app["coverr_api_keys"] = ["coverr-key"]
        config.app.pop("tls_verify", None)
        config.proxy.clear()

        fake_response = SimpleNamespace(json=lambda: {"hits": []})

        with patch(
            "app.services.material.requests.get", return_value=fake_response
        ) as get:
            material.search_videos_coverr("nature", minimum_duration=1)

        self.assertTrue(get.call_args.kwargs["verify"])

    def test_search_coverr_allows_explicit_tls_disable_for_proxy(self):
        """企业自签证书代理场景必须能显式关闭 TLS 校验。"""
        config.app["coverr_api_keys"] = ["coverr-key"]
        config.app["tls_verify"] = False
        config.proxy.clear()

        fake_response = SimpleNamespace(json=lambda: {"hits": []})

        with patch(
            "app.services.material.requests.get", return_value=fake_response
        ) as get:
            material.search_videos_coverr("nature", minimum_duration=1)

        self.assertFalse(get.call_args.kwargs["verify"])

    def test_search_coverr_filters_by_min_duration_and_accepts_string(self):
        """
        Coverr duration 字段在不同响应里可能是 number 或 string,
        两种格式都要接受;低于 minimum_duration 的应被过滤。
        """
        config.app["coverr_api_keys"] = ["coverr-key"]
        config.app.pop("tls_verify", None)
        config.proxy.clear()

        fake_response = SimpleNamespace(
            json=lambda: {
                "hits": [
                    {
                        "id": "shortvid",
                        "duration": 3,  # below minimum
                        "urls": {"mp4_download": "https://example.com/a.mp4"},
                    },
                    {
                        "id": "stringdur",
                        "duration": "10.500000",  # string accepted
                        "urls": {"mp4_download": "https://example.com/b.mp4"},
                    },
                ]
            }
        )

        with patch(
            "app.services.material.requests.get", return_value=fake_response
        ):
            results = material.search_videos_coverr("x", minimum_duration=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].duration, 10)
        self.assertEqual(results[0].url, "https://example.com/b.mp4")

    def test_search_coverr_skips_invalid_items(self):
        """缺 id 或缺 urls.mp4_download 的条目应被跳过,不应抛异常。"""
        config.app["coverr_api_keys"] = ["coverr-key"]
        config.app.pop("tls_verify", None)
        config.proxy.clear()

        fake_response = SimpleNamespace(
            json=lambda: {
                "hits": [
                    {  # missing urls.mp4_download
                        "id": "no-download",
                        "duration": 10,
                        "urls": {"mp4_preview": "https://example.com/preview.mp4"},
                    },
                    {  # missing id
                        "duration": 10,
                        "urls": {"mp4_download": "https://example.com/x.mp4"},
                    },
                    {  # valid baseline
                        "id": "good",
                        "duration": 10,
                        "urls": {"mp4_download": "https://example.com/good.mp4"},
                    },
                ]
            }
        )

        with patch(
            "app.services.material.requests.get", return_value=fake_response
        ):
            results = material.search_videos_coverr("x", minimum_duration=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://example.com/good.mp4")

    def test_search_coverr_returns_empty_on_failure(self):
        """
        响应结构异常 / 网络异常时,函数必须返回 [] 而不是抛异常,
        与 pexels/pixabay 行为保持一致。
        """
        config.app["coverr_api_keys"] = ["coverr-key"]
        config.app.pop("tls_verify", None)
        config.proxy.clear()

        # Subtest A: malformed response (no "hits" key)
        with self.subTest("malformed response"):
            fake_response = SimpleNamespace(
                json=lambda: {"error": "rate limited"}
            )
            with patch(
                "app.services.material.requests.get", return_value=fake_response
            ):
                results = material.search_videos_coverr("x", minimum_duration=1)
            self.assertEqual(results, [])

        # Subtest B: network exception bubbles up from requests.get
        with self.subTest("network exception"):
            with patch(
                "app.services.material.requests.get",
                side_effect=requests.ConnectionError("boom"),
            ):
                results = material.search_videos_coverr("x", minimum_duration=1)
            self.assertEqual(results, [])

    # ---------------- Tests for download_videos coverr branch ----------------

    def test_download_videos_passes_mp4_download_url_to_save_video(self):
        """
        在 source="coverr" 时:
          1. dispatch 到 search_videos_coverr
          2. coverr item 走通用下载路径:save_video 收到的就是 mp4_download URL
             (不再有 coverr://id|url 编码,也不再调用 PATCH ping)
          3. 返回保存路径
        """
        config.app["coverr_api_keys"] = ["coverr-key"]
        config.app.pop("tls_verify", None)
        config.app.pop("material_directory", None)
        config.proxy.clear()

        fake_item = material.MaterialInfo()
        fake_item.provider = "coverr"
        fake_item.url = "https://storage.coverr.co/videos/abc/download?token=xyz"
        fake_item.duration = 10

        with patch(
            "app.services.material.search_videos_coverr",
            return_value=[fake_item],
        ) as search, patch(
            "app.services.material.save_video",
            return_value="/tmp/coverr-saved.mp4",
        ) as save:
            result = material.download_videos(
                task_id="t-coverr",
                search_terms=["nature"],
                source="coverr",
                audio_duration=5,
                max_clip_duration=5,
            )

        # 1. dispatch
        self.assertEqual(search.call_count, 1)

        # 2. save_video 收到的就是 mp4_download URL,原样传入
        save_url = save.call_args.kwargs.get("video_url") or save.call_args.args[0]
        self.assertEqual(
            save_url, "https://storage.coverr.co/videos/abc/download?token=xyz"
        )

        # 3. 返回值正确
        self.assertEqual(result, ["/tmp/coverr-saved.mp4"])


if __name__ == "__main__":
    unittest.main()
