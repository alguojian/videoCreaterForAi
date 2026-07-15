from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.models.schema import VideoAspect, VideoConcatMode, VideoParams, VideoTransitionMode
from app.services import task
from app.utils import utils


TASK_ID = "marriage-test"
SCRIPT = (
    "婚姻二字，写起来只有两笔，走进去却是一生的功课。"
    "它不是找一个完美的人，而是遇见一个愿意一起面对生活的人。"
    "开心时分享，困难时并肩，平凡日子里，也别忘了好好说话。"
    "愿婚姻不是谁改变谁，而是两个人一起，把日子过成彼此安心的模样。"
)


def _run_ffmpeg_background(output: Path, duration: float) -> None:
    filter_graph = (
        "drawbox=x='(iw-520)/2+180*sin(2*PI*t/7)':"
        "y='240+80*cos(2*PI*t/9)':w=520:h=520:"
        "color=0xc07a5a@0.35:t=fill,"
        "drawbox=x='80+120*sin(2*PI*t/8)':"
        "y='1100+100*cos(2*PI*t/6)':w=820:h=820:"
        "color=0x8a4d5f@0.30:t=fill,"
        "drawbox=x=0:y=0:w=iw:h=ih:color=0x000000@0.18:t=fill,"
        "format=yuv420p"
    )
    subprocess.run(
        [
            utils.get_ffmpeg_binary(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x201522:s=1080x1920:r=30",
            "-vf",
            filter_graph,
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
        capture_output=True,
    )


def main() -> int:
    task_dir = Path(utils.task_dir(TASK_ID))
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)

    params = VideoParams(
        video_subject="婚姻二字",
        video_script=SCRIPT,
        video_language="zh-CN",
        voice_name="local:default",
        subtitle_enabled=True,
        video_aspect=VideoAspect.portrait,
        video_count=1,
        video_clip_duration=5,
        video_concat_mode=VideoConcatMode.sequential,
        video_transition_mode=VideoTransitionMode.none,
        font_name="MicrosoftYaHeiBold.ttc",
        font_size=64,
        stroke_width=2,
        text_fore_color="#FFFFFF",
        stroke_color="#24151B",
        text_background_color="#311C2A",
        subtitle_position="bottom",
    )

    audio_file, audio_duration, sub_maker = task.generate_audio(
        TASK_ID, params, SCRIPT
    )
    if not audio_file or not audio_duration or sub_maker is not None:
        raise RuntimeError(
            f"local audio routing failed: audio={audio_file}, duration={audio_duration}, sub_maker={sub_maker}"
        )

    subtitle_path = task.generate_subtitle(
        TASK_ID, params, SCRIPT, sub_maker, audio_file
    )
    if not subtitle_path:
        raise RuntimeError("local Qwen subtitle generation failed")

    background = task_dir / "background.mp4"
    _run_ffmpeg_background(background, float(audio_duration) + 1.0)
    final_videos, combined_videos = task.generate_final_videos(
        TASK_ID, params, [str(background)], audio_file, subtitle_path
    )
    if not final_videos or not Path(final_videos[0]).is_file():
        raise RuntimeError("final marriage test video was not produced")

    final_video = Path(final_videos[0])
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
    print(f"audio={audio_file}")
    print(f"subtitle={subtitle_path}")
    print(f"combined={combined_videos[0]}")
    print(f"final={final_video}")
    print(f"final_bytes={final_video.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
