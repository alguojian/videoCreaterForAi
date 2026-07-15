from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

from app.models.schema import VideoAspect, VideoConcatMode, VideoParams, VideoTransitionMode
from app.services import material, task
from app.utils import utils


TASK_ID = "marriage-pixabay-landscape-test"
REUSE_AUDIO_TASK_ID = "marriage-pixabay-test"
SCRIPT = (
    "婚姻二字，写起来只有两笔，走进去却是一生的功课。"
    "它不是找一个完美的人，而是遇见一个愿意一起面对生活的人。"
    "开心时分享，困难时并肩，平凡日子里，也别忘了好好说话。"
    "愿婚姻不是谁改变谁，而是两个人一起，把日子过成彼此安心的模样。"
)
TERMS = ["wedding couple", "married couple", "family home", "couple holding hands"]


def main() -> int:
    # Pixabay search_videos_pixabay logs its request URL; remove the default sink
    # so the API key is never echoed into the terminal log.
    logger.remove()
    task_dir = Path(utils.task_dir(TASK_ID))
    task_dir.mkdir(parents=True, exist_ok=True)
    config_material_directory = "task"

    from app.config import config

    config.app["material_directory"] = config_material_directory
    params = VideoParams(
        video_subject="婚姻二字",
        video_script=SCRIPT,
        video_language="zh-CN",
        video_source="pixabay",
        voice_name="local:default",
        subtitle_enabled=True,
        video_aspect=VideoAspect.landscape,
        video_count=1,
        video_clip_duration=5,
        video_concat_mode=VideoConcatMode.sequential,
        video_transition_mode=VideoTransitionMode.none,
        font_name="MicrosoftYaHeiBold.ttc",
        emphasis_font_name="SimHei.ttf",
        font_size=64,
        stroke_width=2,
        text_fore_color="#FFFFFF",
        stroke_color="#000000",
        text_background_color=False,
        subtitle_position="bottom",
        emphasis_enabled=True,
        emphasis_terms=(
            "婚姻二字,只有两笔,不是找一个完美的人,一起面对生活,"
            "平凡日子,好好说话,彼此安心"
        ),
        emphasis_random_colors=True,
        emphasis_random_animations=True,
        emphasis_sfx_enabled=True,
        emphasis_sfx_volume=0.5,
    )

    if os.environ.get("MPT_REUSE_SAMPLE_AUDIO") == "1":
        source_dir = PROJECT_ROOT / "storage" / "tasks" / REUSE_AUDIO_TASK_ID
        audio_file = source_dir / "audio.wav"
        subtitle_path = source_dir / "subtitle.srt"
        if not audio_file.is_file() or not subtitle_path.is_file():
            raise RuntimeError("the reusable sample audio or subtitle does not exist")
        audio_duration = task.voice.get_audio_duration(str(audio_file))
        if not audio_duration:
            raise RuntimeError("the reusable sample audio duration is invalid")
    else:
        audio_file, audio_duration, sub_maker = task.generate_audio(
            TASK_ID, params, SCRIPT
        )
        if not audio_file or not audio_duration or sub_maker is not None:
            raise RuntimeError("local audio generation failed")
        subtitle_path = task.generate_subtitle(
            TASK_ID, params, SCRIPT, sub_maker, audio_file
        )
        if not subtitle_path:
            raise RuntimeError("local subtitle generation failed")
    emphasis_path = task.generate_emphasis(TASK_ID, params, SCRIPT, subtitle_path)
    if not emphasis_path or not Path(emphasis_path).is_file():
        raise RuntimeError("emphasis manifest generation failed")

    if os.environ.get("MPT_REUSE_DOWNLOADED_MATERIALS") == "1":
        downloaded = [
            str(video_file)
            for video_file in sorted(task_dir.glob("vid-*.mp4"))
            if video_file.stat().st_size > 0
        ]
    else:
        downloaded = material.download_videos(
            task_id=TASK_ID,
            search_terms=TERMS,
            source="pixabay",
            video_aspect=VideoAspect.landscape,
            video_concat_mode=VideoConcatMode.sequential,
            audio_duration=audio_duration,
            max_clip_duration=params.video_clip_duration,
            match_script_order=True,
        )
    if not downloaded:
        raise RuntimeError(
            "Pixabay returned no usable videos; try English terms or check the API quota"
        )

    final_videos, combined_videos = task.generate_final_videos(
        TASK_ID, params, downloaded, audio_file, subtitle_path, emphasis_path
    )
    final_video = Path(final_videos[0]) if final_videos else None
    if not final_video or not final_video.is_file():
        raise RuntimeError("Pixabay final video was not produced")
    subprocess.run(
        [
            utils.get_ffmpeg_binary(),
            "-v",
            "error",
            "-i",
            str(final_video),
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    print(f"materials={len(downloaded)}")
    print(f"audio={audio_file}")
    print(f"subtitle={subtitle_path}")
    print(f"emphasis={emphasis_path}")
    print(f"combined={combined_videos[0]}")
    print(f"final={final_video}")
    print(f"final_bytes={final_video.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
