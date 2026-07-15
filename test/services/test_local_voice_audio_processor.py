import json
import wave
from pathlib import Path

import pytest

from app.services.local_voice.audio_processor import (
    concatenate_blocks,
    validate_wav,
    write_manifest,
)
from app.services.local_voice.exceptions import AudioProcessingError
from app.services.local_voice.models import PipelineManifest


def _write_wav(path: Path, frames: int, sample_rate: int = 24000, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frames * channels)


def test_validate_wav_returns_duration_and_rejects_wrong_format(tmp_path: Path):
    valid = tmp_path / "valid.wav"
    invalid = tmp_path / "invalid.wav"
    _write_wav(valid, frames=2400)
    _write_wav(invalid, frames=2400, sample_rate=16000)

    assert validate_wav(valid) == pytest.approx(0.1)
    with pytest.raises(AudioProcessingError, match="24kHz mono PCM WAV"):
        validate_wav(invalid)


def test_concatenate_blocks_inserts_configured_silence(tmp_path: Path):
    first = tmp_path / "block_001.wav"
    second = tmp_path / "block_002.wav"
    output = tmp_path / "narration.wav"
    _write_wav(first, frames=2400)
    _write_wav(second, frames=4800)

    duration = concatenate_blocks([first, second], output, pause_ms=100)

    assert duration == pytest.approx(0.4)
    assert validate_wav(output) == pytest.approx(0.4)


def test_concatenate_blocks_reports_missing_input(tmp_path: Path):
    with pytest.raises(AudioProcessingError, match="block audio does not exist"):
        concatenate_blocks([tmp_path / "missing.wav"], tmp_path / "out.wav")


def test_write_manifest_writes_utf8_json(tmp_path: Path):
    path = tmp_path / "manifest.json"
    manifest = PipelineManifest(
        task_id="task-1",
        stages={"tts": "completed"},
        artifacts={"audio": "narration.wav"},
    )

    write_manifest(manifest, path)

    assert json.loads(path.read_text(encoding="utf-8")) == manifest.to_dict()
