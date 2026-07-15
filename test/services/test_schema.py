import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.schema import VideoAspect, VideoParams


class TestVideoAspect(unittest.TestCase):
    def test_to_resolution_known_aspects(self):
        self.assertEqual(VideoAspect.landscape.to_resolution(), (1920, 1080))
        self.assertEqual(VideoAspect.portrait.to_resolution(), (1080, 1920))
        self.assertEqual(VideoAspect.square.to_resolution(), (1080, 1080))

    def test_to_resolution_rejects_unsupported_value(self):
        with self.assertRaises(ValueError):
            VideoAspect.to_resolution("4:5")


class TestVideoParams(unittest.TestCase):
    def test_markdown_script_round_trips_through_json_dump(self):
        params = VideoParams(
            video_subject="婚姻二字",
            markdown_script={
                "title": "婚姻二字",
                "rows": [
                    {
                        "number": 1,
                        "text": "婚姻二字，是两个人共同写下的承诺。",
                        "emphasis_terms": ["婚姻", "承诺"],
                    }
                ],
            },
        )

        restored = VideoParams.model_validate(params.model_dump(mode="json"))

        self.assertEqual(restored.markdown_script.title, "婚姻二字")
        self.assertEqual(restored.markdown_script.rows[0].number, 1)
        self.assertEqual(
            restored.markdown_script.rows[0].emphasis_terms,
            ["婚姻", "承诺"],
        )
        self.assertEqual(
            restored.markdown_script.script_text(),
            "婚姻二字，是两个人共同写下的承诺。",
        )

    def test_defaults_to_landscape_with_emphasis_disabled(self):
        params = VideoParams(video_subject="重点词测试")

        self.assertEqual(params.video_aspect, VideoAspect.landscape.value)
        self.assertFalse(params.emphasis_enabled)
        self.assertEqual(params.emphasis_terms, "")
        self.assertTrue(params.emphasis_random_colors)
        self.assertTrue(params.emphasis_random_animations)
        self.assertTrue(params.emphasis_sfx_enabled)
        self.assertEqual(params.emphasis_sfx_volume, 0.5)

    def test_video_params_uses_project_simhei_for_emphasis(self):
        params = VideoParams(video_subject="婚姻")

        self.assertEqual(params.emphasis_font_name, "SimHei.ttf")
        self.assertNotEqual(params.emphasis_font_name, params.font_name)

    def test_rejects_out_of_range_emphasis_sfx_volume(self):
        with self.assertRaises(ValidationError):
            VideoParams(video_subject="重点词测试", emphasis_sfx_volume=0.51)


if __name__ == "__main__":
    unittest.main()
