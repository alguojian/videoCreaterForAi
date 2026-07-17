from __future__ import annotations

from dataclasses import dataclass


STAGE_RANGES: dict[str, tuple[int, int]] = {
    "script": (0, 10),
    "terms": (10, 20),
    "audio": (20, 35),
    "subtitle": (35, 45),
    "materials": (45, 50),
    "scenes": (50, 75),
    "encoding": (75, 99),
    "complete": (100, 100),
}


@dataclass(frozen=True)
class TaskProgress:
    stage: str
    stage_progress: int
    overall: int
    detail: str = ""


def clamp_stage_progress(value: int | float) -> int:
    return max(0, min(100, int(round(float(value)))))


def build_task_progress(
    stage: str, stage_progress: int | float, detail: str = ""
) -> TaskProgress:
    normalized_stage_progress = clamp_stage_progress(stage_progress)
    bounds = STAGE_RANGES.get(stage)
    if bounds is None:
        overall = normalized_stage_progress
    else:
        start, end = bounds
        overall = round(
            start + (end - start) * normalized_stage_progress / 100
        )
    return TaskProgress(
        stage=stage,
        stage_progress=normalized_stage_progress,
        overall=max(0, min(100, overall)),
        detail=str(detail or ""),
    )
