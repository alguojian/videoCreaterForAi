from __future__ import annotations

import json
import sys
import tempfile
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.local_voice.audio_processor import concatenate_blocks, write_manifest
from app.services.local_voice.models import AlignmentCharacter, PipelineManifest
from app.services.local_voice.subtitle_builder import build_cues, write_srt
from app.services.local_voice.text_normalizer import normalize_narration, split_into_blocks


def _write_wav(path: Path, frames: int) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * frames)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mpt-local-voice-smoke-") as temp:
        root = Path(temp)
        spoken, display = normalize_narration("2026 年，AI 工具价格为 ￥99。")
        blocks = split_into_blocks(spoken, max_chars=100)
        block_paths = []
        for index, block in enumerate(blocks, start=1):
            path = root / f"block_{index:03d}.wav"
            _write_wav(path, 2400)
            block_paths.append(path)
        audio_path = root / "narration.wav"
        duration = concatenate_blocks(block_paths, audio_path, pause_ms=250)
        alignment = [
            AlignmentCharacter(
                char=char,
                index=index,
                start=index * 0.1,
                end=(index + 1) * 0.1,
            )
            for index, char in enumerate(spoken)
            if not char.isspace()
        ]
        cues = build_cues(
            alignment,
            display,
            min_duration=0.1,
            max_duration=4.2,
            max_chars=20,
            audio_duration=max(duration, 2.0),
        )
        subtitle_path = root / "narration.srt"
        write_srt(cues, subtitle_path)
        manifest = PipelineManifest(
            task_id="smoke-test",
            stages={
                "tts": "completed",
                "alignment": "completed",
                "subtitle": "completed",
            },
            artifacts={"audio": str(audio_path), "subtitle": str(subtitle_path)},
        )
        manifest_path = root / "manifest.json"
        write_manifest(manifest, manifest_path)
        assert audio_path.is_file() and subtitle_path.is_file() and manifest_path.is_file()
        json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"audio={audio_path}")
        print(f"subtitle={subtitle_path}")
        print(f"manifest={manifest_path}")
        print(f"duration={duration:.3f}s cues={len(cues)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
