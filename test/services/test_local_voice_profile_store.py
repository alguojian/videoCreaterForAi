import json
import wave
from pathlib import Path

import pytest

from app.services.local_voice.exceptions import InvalidVoiceProfileError
from app.services.local_voice.profile_store import ProfileStore


def _write_wav(path: Path, sample_rate: int = 24000, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * channels * 240)


def test_create_profile_copies_wav_writes_metadata_and_checksum(tmp_path: Path):
    source = tmp_path / "reference-source.wav"
    _write_wav(source)
    store = ProfileStore(tmp_path / "profiles")

    profile = store.create_profile(
        profile_id="digital_project_normal",
        display_name="数字项目部-自然口播",
        reference_text="大家好，这里是数字项目部。",
        source_audio=source,
        default_instruction="自然、清晰。",
    )

    profile_dir = tmp_path / "profiles" / "digital_project_normal"
    assert profile.reference_audio == "reference.wav"
    assert (profile_dir / "reference.wav").exists()
    assert (profile_dir / "checksum.sha256").read_text(encoding="utf-8").strip()
    assert json.loads((profile_dir / "profile.json").read_text(encoding="utf-8"))["profile_id"] == profile.profile_id
    assert store.get_profile(profile.profile_id) == profile


def test_list_profiles_returns_sorted_profile_ids(tmp_path: Path):
    store = ProfileStore(tmp_path / "profiles")
    for profile_id in ("zeta", "alpha"):
        source = tmp_path / f"{profile_id}.wav"
        _write_wav(source)
        store.create_profile(profile_id, profile_id, "测试文本", source)

    assert [profile.profile_id for profile in store.list_profiles()] == ["alpha", "zeta"]


@pytest.mark.parametrize("profile_id", ["..", "a/b", r"C:\absolute", "", "a profile"])
def test_profile_id_cannot_escape_profile_root(tmp_path: Path, profile_id: str):
    source = tmp_path / "reference.wav"
    _write_wav(source)
    store = ProfileStore(tmp_path / "profiles")

    with pytest.raises(InvalidVoiceProfileError, match="invalid profile_id"):
        store.create_profile(profile_id, "测试", "测试文本", source)


def test_missing_profile_and_invalid_audio_are_reported(tmp_path: Path):
    store = ProfileStore(tmp_path / "profiles")

    with pytest.raises(InvalidVoiceProfileError, match="profile not found"):
        store.get_profile("missing")

    invalid_audio = tmp_path / "not-a-wav.bin"
    invalid_audio.write_bytes(b"not wav")
    with pytest.raises(InvalidVoiceProfileError, match="24kHz mono WAV"):
        store.create_profile("bad-audio", "测试", "测试文本", invalid_audio)
