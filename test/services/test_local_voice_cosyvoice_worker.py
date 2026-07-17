from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np

from workers.cosyvoice_worker import generate as cosyvoice_worker
from workers.cosyvoice_worker.generate import _model_dir_for_inference, _save_audio
from app.services.local_voice.service import LocalVoiceSettings


def test_audio_is_written_with_cpu_soundfile(monkeypatch, tmp_path: Path):
    written = {}

    def write(path, audio, sample_rate, subtype):
        written.update(path=path, audio=audio, sample_rate=sample_rate, subtype=subtype)

    monkeypatch.setitem(sys.modules, "soundfile", types.SimpleNamespace(write=write))

    _save_audio(tmp_path / "speech.wav", np.zeros(4, dtype=np.float32), 24000)

    assert written["sample_rate"] == 24000
    assert written["subtype"] == "PCM_16"
    assert written["audio"].shape == (4,)


def test_rl_model_is_staged_as_llm_without_mutating_source(tmp_path: Path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("config", encoding="utf-8")
    (model_dir / "llm.pt").write_bytes(b"base")
    (model_dir / "llm.rl.pt").write_bytes(b"rl")

    with _model_dir_for_inference(model_dir, True) as staged:
        assert (staged / "llm.pt").read_bytes() == b"rl"
        assert (staged / "config.json").read_text(encoding="utf-8") == "config"
        assert staged != model_dir

    assert (model_dir / "llm.pt").read_bytes() == b"base"
    assert (model_dir / "llm.rl.pt").read_bytes() == b"rl"


def test_base_model_can_be_used_without_staging(tmp_path: Path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    with _model_dir_for_inference(model_dir, False) as selected:
        assert selected == model_dir


def test_local_voice_defaults_to_base_model_inference(tmp_path: Path):
    settings = LocalVoiceSettings.from_mapping({}, project_root=tmp_path)

    assert settings.use_rl_model is False

    configured = LocalVoiceSettings.from_mapping(
        {"use_rl_model": True}, project_root=tmp_path
    )
    assert configured.use_rl_model is True


def test_worker_loads_model_once_for_batched_blocks(monkeypatch, tmp_path: Path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    request_path = tmp_path / "request.json"
    output_paths = [tmp_path / "block-1.wav", tmp_path / "block-2.wav"]
    request = {
        "model_dir": str(model_dir),
        "reference_audio": str(reference),
        "reference_text": "参考文本",
        "cosyvoice_repo": "",
        "use_rl_model": False,
        "result_file": str(tmp_path / "result.json"),
        "blocks": [
            {"block_id": "001", "spoken_text": "第一句", "output_wav": str(output_paths[0])},
            {"block_id": "002", "spoken_text": "第二句", "output_wav": str(output_paths[1])},
        ],
    }
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    calls = []

    class FakeCosyVoice:
        sample_rate = 24000

        def inference_zero_shot(self, text, prompt, reference_audio, stream=False):
            calls.append(text)
            return [{"tts_speech": np.zeros(240, dtype=np.float32)}]

    class FakeAutoModel:
        def __new__(cls, **kwargs):
            calls.append("model")
            return FakeCosyVoice()

    cosyvoice_package = types.ModuleType("cosyvoice")
    cosyvoice_cli = types.ModuleType("cosyvoice.cli")
    cosyvoice_module = types.ModuleType("cosyvoice.cli.cosyvoice")
    cosyvoice_module.AutoModel = FakeAutoModel
    monkeypatch.setitem(sys.modules, "cosyvoice", cosyvoice_package)
    monkeypatch.setitem(sys.modules, "cosyvoice.cli", cosyvoice_cli)
    monkeypatch.setitem(sys.modules, "cosyvoice.cli.cosyvoice", cosyvoice_module)
    monkeypatch.setattr(cosyvoice_worker, "_install_soundfile_audio_loader", lambda: None)
    monkeypatch.setattr(
        cosyvoice_worker,
        "_save_audio",
        lambda path, audio, sample_rate: Path(path).write_bytes(b"audio"),
    )

    result = cosyvoice_worker.run(request_path)

    assert result["status"] == "completed"
    assert result["blocks_completed"] == 2
    assert calls == ["model", "第一句", "第二句"]
    assert all(path.is_file() for path in output_paths)
