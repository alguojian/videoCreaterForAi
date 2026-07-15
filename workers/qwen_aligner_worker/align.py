from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_request(request_path: Path) -> dict[str, Any]:
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    for field in ("model_dir", "audio_file", "spoken_text"):
        if not payload.get(field):
            raise ValueError(f"missing request field: {field}")
    return payload


def _expand_item(text: str, start: float, end: float, offset: int) -> list[dict[str, Any]]:
    characters = list(text)
    if not characters:
        return []
    step = (end - start) / len(characters)
    return [
        {
            "char": char,
            "index": offset + index,
            "start": round(start + index * step, 6),
            "end": round(start + (index + 1) * step, 6),
        }
        for index, char in enumerate(characters)
    ]


def run(request_path: Path) -> dict[str, Any]:
    request = _load_request(request_path)
    model_dir = Path(request["model_dir"]).expanduser().resolve()
    audio_file = Path(request["audio_file"]).expanduser().resolve()
    if not model_dir.is_dir():
        raise FileNotFoundError(f"Qwen aligner model directory does not exist: {model_dir}")
    if not audio_file.is_file():
        raise FileNotFoundError(f"audio file does not exist: {audio_file}")

    import torch
    from qwen_asr import Qwen3ForcedAligner

    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    device_map = "cuda:0" if torch.cuda.is_available() else "cpu"
    model = Qwen3ForcedAligner.from_pretrained(
        str(model_dir),
        dtype=dtype,
        device_map=device_map,
    )
    results = model.align(
        audio=str(audio_file),
        text=request["spoken_text"],
        language=request.get("language", "Chinese"),
    )
    if not results:
        raise RuntimeError("Qwen ForcedAligner returned no result")
    alignment: list[dict[str, Any]] = []
    for item in results[0]:
        alignment.extend(
            _expand_item(
                str(item.text),
                float(item.start_time),
                float(item.end_time),
                len(alignment),
            )
        )
    if not alignment:
        raise RuntimeError("Qwen ForcedAligner returned no aligned characters")
    return {
        "status": "completed",
        "language": request.get("language", "Chinese"),
        "audio_file": str(audio_file),
        "alignment": alignment,
        "model_dir": str(model_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result_path = Path(request["result_file"])
    try:
        result = run(args.request)
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception as exc:
        print(f"Qwen aligner worker failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
