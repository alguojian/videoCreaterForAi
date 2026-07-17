from app.services.progress import build_task_progress, clamp_stage_progress


def test_stage_progress_is_clamped_and_mapped_to_overall_progress():
    assert clamp_stage_progress(-5) == 0
    assert clamp_stage_progress(125) == 100
    progress = build_task_progress("audio", 50, "CosyVoice block 2/4")

    assert progress.overall == 28
    assert progress.stage == "audio"
    assert progress.stage_progress == 50
    assert progress.detail == "CosyVoice block 2/4"


def test_unknown_stage_is_safe_for_legacy_callers():
    progress = build_task_progress("legacy", 40, "old task")

    assert progress.overall == 40
    assert progress.stage == "legacy"
