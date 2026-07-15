from __future__ import annotations

import argparse
import sys
import tempfile
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.local_voice.worker_runner import run_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--cosyvoice-repo", required=True, type=Path)
    parser.add_argument("--reference-audio", required=True, type=Path)
    parser.add_argument(
        "--reference-text",
        default="希望你以后能够做的比我还好呦。",
    )
    parser.add_argument(
        "--text",
        default="这是一个本地 CosyVoice 语音合成测试。",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mpt-cosyvoice-worker-smoke-") as temp:
        output_wav = Path(temp) / "generated.wav"
        result = run_worker(
            args.python,
            PROJECT_ROOT / "workers" / "cosyvoice_worker" / "generate.py",
            {
                "model_dir": str(args.model_dir),
                "reference_audio": str(args.reference_audio),
                "reference_text": args.reference_text,
                "spoken_text": args.text,
                "cosyvoice_repo": str(args.cosyvoice_repo),
                "output_wav": str(output_wav),
            },
            temp,
            project_root=PROJECT_ROOT,
            timeout_seconds=1800,
        )
        if result.get("status") != "completed" or not output_wav.is_file():
            raise RuntimeError(f"unexpected CosyVoice result: {result}")
        with wave.open(str(output_wav), "rb") as audio:
            print(
                f"status={result['status']} sample_rate={audio.getframerate()} "
                f"channels={audio.getnchannels()} frames={audio.getnframes()}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
