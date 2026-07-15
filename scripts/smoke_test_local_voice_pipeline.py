from __future__ import annotations

import tempfile
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import config
from app.models.schema import VideoAspect, VideoParams
from app.services import local_voice
from app.services import subtitle
from app.services import video
from app.utils import utils


def main() -> int:
    settings = local_voice.settings_from_config(
        dict(config.local_voice), utils.root_dir()
    )
    if not settings.enabled:
        raise RuntimeError("[local_voice].enabled must be true for this smoke test")

    with tempfile.TemporaryDirectory(prefix="mpt-local-voice-pipeline-") as temp:
        task_dir = Path(temp)
        service = local_voice.LocalVoiceService(settings)
        audio = service.synthesize(
            "local-voice-pipeline-smoke",
            task_dir,
            "这是 MoneyPrinterTurbo 的本地语音和字幕链路测试。",
            "local:default",
        )
        subtitle_path = service.align_subtitle(
            "local-voice-pipeline-smoke",
            task_dir,
            audio.audio_file,
            "这是 MoneyPrinterTurbo 的本地语音和字幕链路测试。",
            language="Chinese",
        )
        subtitles = subtitle.file_to_subtitles(str(subtitle_path))
        if not audio.audio_file.is_file() or not subtitles:
            raise RuntimeError("local voice pipeline did not produce valid artifacts")

        source_video = task_dir / "source.mp4"
        final_video = task_dir / "final.mp4"
        subprocess.run(
            [
                utils.get_ffmpeg_binary(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x203040:s=720x1280:r=30",
                "-t",
                "8",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source_video),
            ],
            check=True,
            capture_output=True,
        )
        video.generate_video(
            str(source_video),
            str(audio.audio_file),
            "",
            str(final_video),
            VideoParams(
                video_subject="local voice MP4 smoke",
                video_aspect=VideoAspect.portrait,
                subtitle_enabled=False,
            ),
        )
        if not final_video.is_file() or final_video.stat().st_size == 0:
            raise RuntimeError("final MP4 was not produced")
        print(
            f"audio={audio.audio_file} duration={audio.duration:.3f}s "
            f"subtitle={subtitle_path} cues={len(subtitles)} "
            f"mp4={final_video} bytes={final_video.stat().st_size}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
