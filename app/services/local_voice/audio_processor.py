from __future__ import annotations

import json
import wave
from pathlib import Path

from .exceptions import AudioProcessingError
from .models import PipelineManifest


DEFAULT_SAMPLE_RATE = 24000
DEFAULT_CHANNELS = 1
DEFAULT_SAMPLE_WIDTH = 2


def validate_wav(
    audio_path: str | Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    sample_width: int = DEFAULT_SAMPLE_WIDTH,
) -> float:
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise AudioProcessingError(f"block audio does not exist: {path}")
    try:
        with wave.open(str(path), "rb") as audio:
            actual = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth())
            expected = (sample_rate, channels, sample_width)
            if actual != expected:
                raise AudioProcessingError(
                    "audio must be a 24kHz mono PCM WAV; "
                    f"expected {expected}, received {actual}: {path}"
                )
            return audio.getnframes() / audio.getframerate()
    except AudioProcessingError:
        raise
    except (wave.Error, EOFError) as exc:
        raise AudioProcessingError(f"audio must be a 24kHz mono PCM WAV: {path}") from exc


def concatenate_blocks(
    block_paths: list[str | Path],
    output_path: str | Path,
    *,
    pause_ms: int = 250,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    sample_width: int = DEFAULT_SAMPLE_WIDTH,
) -> float:
    if not block_paths:
        raise AudioProcessingError("at least one audio block is required")
    if pause_ms < 0:
        raise AudioProcessingError("pause_ms must be non-negative")

    paths = [Path(path).expanduser().resolve() for path in block_paths]
    for path in paths:
        validate_wav(
            path,
            sample_rate=sample_rate,
            channels=channels,
            sample_width=sample_width,
        )

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    silence_frames = round(sample_rate * pause_ms / 1000)
    silence = b"\x00" * silence_frames * channels * sample_width
    total_frames = 0

    try:
        with wave.open(str(output), "wb") as combined:
            combined.setnchannels(channels)
            combined.setsampwidth(sample_width)
            combined.setframerate(sample_rate)
            for index, path in enumerate(paths):
                with wave.open(str(path), "rb") as block:
                    frames = block.readframes(block.getnframes())
                    combined.writeframes(frames)
                    total_frames += len(frames) // (channels * sample_width)
                if index < len(paths) - 1 and silence:
                    combined.writeframes(silence)
                    total_frames += silence_frames
    except (OSError, wave.Error) as exc:
        raise AudioProcessingError(f"failed to concatenate WAV blocks: {output}") from exc

    return total_frames / sample_rate


def write_manifest(manifest: PipelineManifest, output_path: str | Path) -> None:
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise AudioProcessingError(f"failed to write manifest: {output}") from exc
