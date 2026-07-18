from __future__ import annotations

import wave
from pathlib import Path

from app.services.local_voice.profile_store import ProfileStore
from app.services.local_voice.service import LocalVoiceService, LocalVoiceSettings


def _write_wav(path: Path, frames: int = 24000) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * frames)


def test_synthesize_uses_profile_and_writes_manifest(tmp_path: Path):
    reference = tmp_path / "source.wav"
    _write_wav(reference, 2400)
    profile = ProfileStore(tmp_path / "profiles").create_profile(
        "speaker",
        "Test speaker",
        "这是参考音频。",
        reference,
    )
    settings = LocalVoiceSettings.from_mapping(
        {
            "cosyvoice_python": "tools/cosyvoice312/python.exe",
            "cosyvoice_worker": "workers/cosyvoice_worker/generate.py",
            "cosyvoice_model_dir": "resource/models/cosyvoice",
            "voice_profile_root": str(tmp_path / "profiles"),
            "default_voice_profile": "speaker",
            "block_max_chars": 4,
            "block_pause_ms": 100,
        },
        project_root=tmp_path,
    )
    requests = []

    def fake_runner(python, worker, request, task_dir, **kwargs):
        requests.append(request)
        for block in request["blocks"]:
            _write_wav(Path(block["output_wav"]), 2400)
        return {
            "status": "completed",
            "sample_rate": 24000,
            "blocks_completed": len(request["blocks"]),
        }

    service = LocalVoiceService(settings, runner=fake_runner)
    result = service.synthesize("task-1", tmp_path / "task-1", "第一句。第二句。", "local:speaker")

    assert result.audio_file.is_file()
    assert result.duration == 0.3
    assert len(requests) == 1
    assert requests[0]["reference_text"] == profile.reference_text
    assert requests[0]["instruction"] == profile.default_instruction
    assert requests[0]["blocks"][0]["spoken_text"] == "第一句。"
    assert (tmp_path / "task-1" / "local_voice_manifest.json").is_file()


def test_synthesize_batches_all_blocks_into_one_worker_request(tmp_path: Path):
    reference = tmp_path / "source.wav"
    _write_wav(reference, 2400)
    cosyvoice_repo = tmp_path / "cosyvoice-repo"
    (cosyvoice_repo / "asset").mkdir(parents=True)
    _write_wav(cosyvoice_repo / "asset" / "zero_shot_prompt.wav", 2400)
    settings = LocalVoiceSettings.from_mapping(
        {
            "cosyvoice_python": "tools/cosyvoice312/python.exe",
            "cosyvoice_worker": "workers/cosyvoice_worker/generate.py",
            "cosyvoice_model_dir": "resource/models/cosyvoice",
            "cosyvoice_repo": str(cosyvoice_repo),
            "voice_profile_root": str(tmp_path / "profiles"),
            "block_max_chars": 4,
        },
        project_root=tmp_path,
    )
    requests = []

    def fake_runner(python, worker, request, task_dir, **kwargs):
        requests.append(request)
        for block in request["blocks"]:
            _write_wav(Path(block["output_wav"]), 2400)
        return {"status": "completed", "blocks_completed": len(request["blocks"])}

    service = LocalVoiceService(settings, runner=fake_runner)
    result = service.synthesize("task-batch", tmp_path / "task-batch", "第一句。第二句。")

    assert result.audio_file.is_file()
    assert len(requests) == 1
    assert len(requests[0]["blocks"]) == 2
    assert requests[0]["blocks"][0]["spoken_text"] == "第一句。"


def test_align_subtitle_writes_srt(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    _write_wav(audio, 48000)
    settings = LocalVoiceSettings.from_mapping(
        {
            "aligner_python": "tools/miniforge3/envs/qwen-aligner/python.exe",
            "aligner_worker": "workers/qwen_aligner_worker/align.py",
            "aligner_model_dir": "resource/models/qwen",
        },
        project_root=tmp_path,
    )

    def fake_runner(python, worker, request, task_dir, **kwargs):
        return {
            "status": "completed",
            "alignment": [
                {"char": "你", "index": 0, "start": 0.0, "end": 0.4},
                {"char": "好", "index": 1, "start": 0.4, "end": 0.8},
            ],
        }

    service = LocalVoiceService(settings, runner=fake_runner)
    subtitle = service.align_subtitle(
        "task-2", tmp_path / "task-2", audio, "你好", language="Chinese"
    )

    assert subtitle.is_file()
    assert "你好" in subtitle.read_text(encoding="utf-8")


def test_align_subtitle_repairs_zero_length_alignment(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    _write_wav(audio, 48000)
    settings = LocalVoiceSettings.from_mapping({}, project_root=tmp_path)

    def fake_runner(python, worker, request, task_dir, **kwargs):
        return {
            "status": "completed",
            "alignment": [
                {"char": "婚", "index": 0, "start": 0.0, "end": 0.4},
                {"char": "姻", "index": 1, "start": 0.4, "end": 0.4},
                {"char": "。", "index": 2, "start": 0.4, "end": 0.8},
            ],
        }

    service = LocalVoiceService(settings, runner=fake_runner)
    subtitle = service.align_subtitle(
        "task-zero-duration", tmp_path / "task", audio, "婚姻。", language="Chinese"
    )

    assert subtitle.is_file()
    assert "婚姻" in subtitle.read_text(encoding="utf-8")


def test_settings_resolve_relative_paths(tmp_path: Path):
    settings = LocalVoiceSettings.from_mapping(
        {"cosyvoice_model_dir": "resource/models/cosyvoice"},
        project_root=tmp_path,
    )

    assert settings.cosyvoice_model_dir == tmp_path / "resource/models/cosyvoice"
    assert settings.subtitle_provider == "qwen_forced_aligner"


def test_settings_use_larger_default_blocks_without_overriding_explicit_value(
    tmp_path: Path,
):
    assert LocalVoiceSettings.from_mapping({}, project_root=tmp_path).block_max_chars == 200
    explicit = LocalVoiceSettings.from_mapping(
        {"block_max_chars": 100}, project_root=tmp_path
    )
    assert explicit.block_max_chars == 100
