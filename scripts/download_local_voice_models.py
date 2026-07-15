from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import quote


MODELS = {
    "cosyvoice": (
        "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "Fun-CosyVoice3-0.5B-2512",
    ),
    "qwen": ("Qwen/Qwen3-ForcedAligner-0.6B", "Qwen3-ForcedAligner-0.6B"),
}


def _download_with_curl(repo_id: str, target: Path) -> None:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl.exe is required for the direct Hugging Face fallback")

    api = f"https://huggingface.co/api/models/{repo_id}"
    result = subprocess.run(
        [curl, "-L", "--fail", "--retry", "3", "--retry-all-errors", api],
        check=True,
        capture_output=True,
        text=True,
    )
    siblings = json.loads(result.stdout).get("siblings", [])
    files = [item for item in siblings if item.get("rfilename")]
    if not files:
        raise RuntimeError(f"No files listed for Hugging Face repository: {repo_id}")

    target.mkdir(parents=True, exist_ok=True)
    for item in files:
        filename = item["rfilename"]
        destination = target / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{repo_id}/resolve/main/{quote(filename, safe='')}"
        size = item.get("size") or _remote_size_with_curl(curl, url)
        if isinstance(size, int) and destination.exists() and destination.stat().st_size == size:
            print(f"Already downloaded {filename}", flush=True)
            continue
        if size is None and destination.exists():
            print(f"Keeping existing {filename}; remote size unavailable", flush=True)
            continue
        if isinstance(size, int) and size >= 64 * 1024 * 1024:
            _download_large_file_in_ranges(curl, url, destination, size)
            continue
        _download_small_file(curl, url, destination)


def _download_small_file(curl: str, url: str, destination: Path) -> None:
    print(f"Downloading {destination.name} with curl.exe", flush=True)
    command = [
        curl,
        "-L",
        "--fail",
        "--retry",
        "5",
        "--retry-all-errors",
        "--max-time",
        "180",
        "--continue-at",
        "-",
        "--output",
        str(destination),
        url,
    ]
    last_error = None
    for attempt in range(1, 9):
        result = subprocess.run(command, check=False)
        if result.returncode == 0:
            return
        last_error = result.returncode
        print(f"Small file download retry {attempt}/8 (curl exit {last_error})", flush=True)
        time.sleep(2)
    raise subprocess.CalledProcessError(last_error or 1, command)


def _remote_size_with_curl(curl: str, url: str) -> int | None:
    result = subprocess.run(
        [curl, "-sS", "-I", "-L", "--retry", "5", "--retry-all-errors", "--max-time", "60", url],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    for line in reversed(result.stdout.splitlines()):
        if line.lower().startswith("content-length:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _download_large_file_in_ranges(
    curl: str, url: str, destination: Path, total_size: int
) -> None:
    chunk_size = 16 * 1024 * 1024
    part_path = destination.with_name(destination.name + ".part")
    start = destination.stat().st_size if destination.exists() else 0
    if start > total_size:
        raise RuntimeError(f"Existing file is larger than expected: {destination}")
    print(
        f"Downloading {destination.name} in 16 MB ranges ({start}/{total_size})",
        flush=True,
    )
    while start < total_size:
        end = min(total_size - 1, start + chunk_size - 1)
        part_path.unlink(missing_ok=True)
        subprocess.run(
            [
                curl,
                "-L",
                "--fail",
                "--retry",
                "5",
                "--retry-all-errors",
                "--max-time",
                "180",
                "--speed-limit",
                "10000",
                "--speed-time",
                "20",
                "--range",
                f"{start}-{end}",
                "--output",
                str(part_path),
                url,
            ],
            check=True,
        )
        expected = end - start + 1
        actual = part_path.stat().st_size
        if actual != expected:
            raise RuntimeError(
                f"Range length mismatch for {destination}: expected {expected}, got {actual}"
            )
        with part_path.open("rb") as source, destination.open("ab") as output:
            while data := source.read(1024 * 1024):
                output.write(data)
        part_path.unlink()
        start = destination.stat().st_size
        print(f"Downloaded {destination.name}: {start}/{total_size}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--model",
        action="append",
        choices=sorted(MODELS),
        dest="models",
        help="Model to download; repeat for both models. Defaults to both.",
    )
    args = parser.parse_args()
    selected = args.models or sorted(MODELS)
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    from huggingface_hub import snapshot_download

    for name in selected:
        repo_id, directory_name = MODELS[name]
        target = root / directory_name
        print(f"Downloading {repo_id} to {target}", flush=True)
        has_partial_download = target.exists() and any(target.rglob("*.part"))
        if has_partial_download:
            print("Partial files detected; continuing directly with curl.exe", flush=True)
            _download_with_curl(repo_id, target)
        else:
            try:
                snapshot_download(
                    repo_id=repo_id,
                    local_dir=str(target),
                    local_dir_use_symlinks=False,
                    max_workers=2,
                )
            except Exception as exc:
                print(f"Hub metadata download failed ({exc}); retrying with curl.exe", flush=True)
                _download_with_curl(repo_id, target)
        print(f"Completed {name}: {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
