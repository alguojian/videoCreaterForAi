import json
import sys
from pathlib import Path

import pytest

from app.services.local_voice.exceptions import WorkerExecutionError
from app.services.local_voice.worker_runner import run_worker


def _write_worker(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_run_worker_writes_request_collects_logs_and_reads_result(tmp_path: Path):
    worker = _write_worker(
        tmp_path / "fake_worker.py",
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[2])
request = json.loads(request_path.read_text(encoding='utf-8'))
Path(request['result_file']).write_text(json.dumps({'status': 'ok', 'value': request['value']}), encoding='utf-8')
print('worker stdout')
print('worker stderr', file=sys.stderr)
""",
    )

    result = run_worker(
        python_executable=Path(sys.executable),
        worker_script=worker,
        request={"value": "ready"},
        task_dir=tmp_path / "task",
        timeout_seconds=5,
    )

    assert result == {"status": "ok", "value": "ready"}
    assert "worker stdout" in (tmp_path / "task" / "fake_worker.stdout.log").read_text()
    assert "worker stderr" in (tmp_path / "task" / "fake_worker.stderr.log").read_text()


def test_run_worker_rejects_relative_python_path(tmp_path: Path):
    with pytest.raises(WorkerExecutionError, match="absolute Python executable"):
        run_worker("python.exe", tmp_path / "worker.py", {}, tmp_path / "task")


def test_run_worker_reports_nonzero_exit_and_keeps_stderr(tmp_path: Path):
    worker = _write_worker(
        tmp_path / "failing_worker.py",
        "import sys; print('failure detail', file=sys.stderr); raise SystemExit(7)",
    )

    with pytest.raises(WorkerExecutionError, match="exit code 7"):
        run_worker(Path(sys.executable), worker, {}, tmp_path / "task", timeout_seconds=5)

    assert "failure detail" in (tmp_path / "task" / "failing_worker.stderr.log").read_text()


def test_run_worker_reports_timeout(tmp_path: Path):
    worker = _write_worker(
        tmp_path / "slow_worker.py",
        "import time; time.sleep(1)",
    )

    with pytest.raises(WorkerExecutionError, match="timed out"):
        run_worker(Path(sys.executable), worker, {}, tmp_path / "task", timeout_seconds=0.05)


def test_run_worker_reports_missing_result(tmp_path: Path):
    worker = _write_worker(tmp_path / "empty_worker.py", "pass")

    with pytest.raises(WorkerExecutionError, match="result file is missing"):
        run_worker(Path(sys.executable), worker, {}, tmp_path / "task", timeout_seconds=5)
