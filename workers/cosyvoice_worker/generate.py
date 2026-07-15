from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


def _load_request(request_path: Path) -> dict[str, Any]:
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    for field in ("model_dir", "reference_audio", "reference_text", "spoken_text", "output_wav"):
        if not payload.get(field):
            raise ValueError(f"missing request field: {field}")
    return payload


def _save_audio(output_path: Path, audio: Any, sample_rate: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        import torchaudio

        torchaudio.save(str(output_path), audio, sample_rate)
        return

    if hasattr(audio, "detach"):
        array = audio.detach().cpu().numpy()
    else:
        array = np.asarray(audio)
    if array.ndim == 2 and array.shape[0] == 1:
        array = array[0]
    sf.write(str(output_path), array, sample_rate, subtype="PCM_16")


def _install_soundfile_audio_loader() -> None:
    """Avoid torchaudio 2.11's TorchCodec/FFmpeg DLL requirement on Windows."""
    try:
        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio
    except ImportError:
        return

    def load_audio(path: str | Path, *args: Any, **kwargs: Any):
        data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
        audio = torch.from_numpy(np.ascontiguousarray(data.T))
        if kwargs.get("channels_first", True):
            return audio, sample_rate
        return audio.transpose(0, 1), sample_rate

    torchaudio.load = load_audio


@contextmanager
def _model_dir_for_inference(model_dir: Path, use_rl_model: bool):
    if not use_rl_model:
        yield model_dir
        return
    rl_checkpoint = model_dir / "llm.rl.pt"
    if not rl_checkpoint.is_file():
        raise FileNotFoundError(f"RL CosyVoice checkpoint does not exist: {rl_checkpoint}")

    with tempfile.TemporaryDirectory(
        prefix=f".{model_dir.name}.rl-stage-", dir=str(model_dir.parent)
    ) as staging:
        staging_dir = Path(staging)
        for source in model_dir.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(model_dir)
            effective_source = rl_checkpoint if relative == Path("llm.pt") else source
            destination = staging_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(effective_source, destination)
            except OSError:
                shutil.copy2(effective_source, destination)
        yield staging_dir


def run(request_path: Path) -> dict[str, Any]:
    request = _load_request(request_path)
    model_dir = Path(request["model_dir"]).expanduser().resolve()
    reference_audio = Path(request["reference_audio"]).expanduser().resolve()
    output_wav = Path(request["output_wav"]).expanduser().resolve()
    if not model_dir.is_dir():
        raise FileNotFoundError(f"CosyVoice model directory does not exist: {model_dir}")
    if not reference_audio.is_file():
        raise FileNotFoundError(f"reference audio does not exist: {reference_audio}")

    cosyvoice_repo = request.get("cosyvoice_repo") or os.environ.get("COSYVOICE_REPO")
    if cosyvoice_repo:
        repo = Path(cosyvoice_repo).expanduser().resolve()
        sys.path.insert(0, str(repo))
        matcha = repo / "third_party" / "Matcha-TTS"
        if matcha.is_dir():
            sys.path.insert(0, str(matcha))

    _install_soundfile_audio_loader()
    from cosyvoice.cli.cosyvoice import AutoModel

    use_rl_model = bool(request.get("use_rl_model", True))
    with _model_dir_for_inference(model_dir, use_rl_model) as inference_model_dir:
        cosyvoice = AutoModel(model_dir=str(inference_model_dir))
        instruction = (request.get("instruction") or "You are a helpful assistant.").strip()
        prompt_text = instruction
        if "<|endofprompt|>" not in prompt_text:
            prompt_text += "<|endofprompt|>"
        prompt_text += request["reference_text"]
        chunks = cosyvoice.inference_zero_shot(
            request["spoken_text"],
            prompt_text,
            str(reference_audio),
            stream=False,
        )
        generated = list(chunks)
        if not generated:
            raise RuntimeError("CosyVoice returned no audio chunks")
        sample_rate = int(getattr(cosyvoice, "sample_rate", 24000))
        _save_audio(output_wav, generated[0]["tts_speech"], sample_rate)
    return {
        "status": "completed",
        "audio_file": str(output_wav),
        "sample_rate": sample_rate,
        "model_dir": str(model_dir),
        "use_rl_model": use_rl_model,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    result_path = Path(json.loads(args.request.read_text(encoding="utf-8"))["result_file"])
    try:
        result = run(args.request)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception as exc:
        print(f"CosyVoice worker failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
