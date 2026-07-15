import json

import pytest

from app.services.local_voice.models import (
    AlignmentCharacter,
    NarrationBlock,
    PipelineManifest,
    SubtitleCue,
    VoiceProfile,
)


def test_narration_block_round_trips_json_ready_data():
    block = NarrationBlock(
        block_id="block_001",
        spoken_text="二零二六年七月。",
        display_text="2026 年 7 月。",
        pause_after_ms=250,
    )

    payload = json.loads(json.dumps(block.to_dict(), ensure_ascii=False))

    assert NarrationBlock.from_dict(payload) == block


def test_alignment_character_rejects_non_positive_duration():
    with pytest.raises(ValueError, match="end must be greater than start"):
        AlignmentCharacter(char="你", index=0, start=1.0, end=1.0)


def test_subtitle_cue_requires_positive_one_based_index():
    with pytest.raises(ValueError, match="index must be positive"):
        SubtitleCue(index=0, start=0.0, end=1.0, text="测试")


def test_voice_profile_serializes_paths_as_strings():
    profile = VoiceProfile(
        profile_id="digital_project_normal",
        display_name="数字项目部-自然口播",
        reference_audio=r"resource\voice_profiles\digital_project_normal\reference.wav",
        reference_text="大家好，这里是数字项目部。",
        default_instruction="自然、清晰。",
        checksum_sha256="abc123",
    )

    payload = profile.to_dict()

    assert payload["reference_audio"].endswith("reference.wav")
    assert VoiceProfile.from_dict(payload) == profile


def test_manifest_rejects_unknown_stage():
    with pytest.raises(ValueError, match="unsupported manifest stage"):
        PipelineManifest(task_id="task-1", stages={"tts": "unknown"})


def test_manifest_round_trips_artifact_state():
    manifest = PipelineManifest(
        task_id="task-1",
        stages={"tts": "completed", "alignment": "degraded"},
        artifacts={"audio": "narration.wav", "subtitle": "narration.srt"},
        alignment_degraded=True,
    )

    assert PipelineManifest.from_dict(manifest.to_dict()) == manifest
