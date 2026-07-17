from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from .exceptions import WorkerExecutionError


def run_worker(
    python_executable: str | Path,
    worker_script: str | Path,
    request: dict[str, Any],
    task_dir: str | Path,
    *,
    timeout_seconds: float = 600,
    project_root: str | Path | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    python_path = Path(python_executable).expanduser()
    if not python_path.is_absolute() or not python_path.is_file():
        raise WorkerExecutionError(
            f"worker requires an absolute Python executable: {python_executable}"
        )
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    script_path = Path(worker_script).expanduser()
    if not script_path.is_absolute():
        script_path = root / script_path
    script_path = script_path.resolve()
    if not script_path.is_file():
        raise WorkerExecutionError(f"worker script does not exist: {script_path}")
    if timeout_seconds <= 0:
        raise WorkerExecutionError("worker timeout must be positive")

    output_dir = Path(task_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    worker_name = script_path.stem
    request_path = output_dir / f"{worker_name}.request.json"
    result_path = output_dir / f"{worker_name}.result.json"
    payload = dict(request)
    payload["result_file"] = str(result_path)
    request_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    command = [str(python_path), str(script_path), "--request", str(request_path)]
    stdout = ""
    stderr = ""
    stop_progress = threading.Event()
    progress_thread = None
    blocks = request.get("blocks") or []
    if progress_callback and blocks:
        def watch_block_outputs():
            total = len(blocks)
            last_completed = -1
            while not stop_progress.wait(0.5):
                completed_blocks = sum(
                    1 for block in blocks if Path(block["output_wav"]).is_file()
                )
                if completed_blocks != last_completed:
                    progress_callback(
                        "audio",
                        completed_blocks,
                        total,
                        f"CosyVoice block {completed_blocks}/{total}",
                    )
                    last_completed = completed_blocks

        progress_thread = threading.Thread(
            target=watch_block_outputs,
            name="local-voice-progress",
            daemon=True,
        )
        progress_thread.start()
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
        _write_logs(output_dir, worker_name, stdout, stderr)
        raise WorkerExecutionError(
            f"worker timed out after {timeout_seconds} seconds: {worker_name}"
        ) from exc
    except OSError as exc:
        _write_logs(output_dir, worker_name, stdout, stderr)
        raise WorkerExecutionError(f"failed to start worker: {worker_name}") from exc
    finally:
        stop_progress.set()
        if progress_thread:
            progress_thread.join(timeout=1.0)

    _write_logs(output_dir, worker_name, stdout, stderr)
    if completed.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "no worker output"
        raise WorkerExecutionError(
            f"worker failed with exit code {completed.returncode}: {detail}"
        )
    if not result_path.is_file():
        raise WorkerExecutionError(f"worker result file is missing: {result_path}")
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerExecutionError(f"worker result file is invalid: {result_path}") from exc


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _write_logs(output_dir: Path, worker_name: str, stdout: str, stderr: str) -> None:
    (output_dir / f"{worker_name}.stdout.log").write_text(stdout, encoding="utf-8")
    (output_dir / f"{worker_name}.stderr.log").write_text(stderr, encoding="utf-8")
