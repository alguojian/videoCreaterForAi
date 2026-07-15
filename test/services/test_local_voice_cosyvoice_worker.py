from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np

from workers.cosyvoice_worker.generate import _model_dir_for_inference, _save_audio


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
