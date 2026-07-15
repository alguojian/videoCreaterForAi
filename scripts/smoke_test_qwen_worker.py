from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.local_voice.worker_runner import run_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument(
        "--text",
        default="希望你以后能够做的比我还好呦。",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mpt-qwen-worker-smoke-") as temp:
        result = run_worker(
            args.python,
            PROJECT_ROOT / "workers" / "qwen_aligner_worker" / "align.py",
            {
                "model_dir": str(args.model_dir),
                "audio_file": str(args.audio),
                "spoken_text": args.text,
                "language": "Chinese",
            },
            temp,
            project_root=PROJECT_ROOT,
            timeout_seconds=900,
        )
        alignment = result.get("alignment", [])
        if result.get("status") != "completed" or not alignment:
            raise RuntimeError(f"unexpected Qwen result: {result}")
        print(f"status={result['status']} chars={len(alignment)}")
        print(f"first={alignment[0]}")
        print(f"last={alignment[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
