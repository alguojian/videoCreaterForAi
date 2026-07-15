from __future__ import annotations

import hashlib
import json
import re
import shutil
import wave
from pathlib import Path

from .exceptions import InvalidVoiceProfileError
from .models import VoiceProfile


_PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ProfileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def _validate_profile_id(self, profile_id: str) -> str:
        if not _PROFILE_ID_PATTERN.fullmatch(profile_id or ""):
            raise InvalidVoiceProfileError(f"invalid profile_id: {profile_id!r}")
        return profile_id

    def _profile_dir(self, profile_id: str) -> Path:
        safe_id = self._validate_profile_id(profile_id)
        profile_dir = (self.root / safe_id).resolve()
        try:
            profile_dir.relative_to(self.root)
        except ValueError as exc:
            raise InvalidVoiceProfileError(f"invalid profile_id: {profile_id!r}") from exc
        return profile_dir

    @staticmethod
    def validate_reference_audio(source_audio: str | Path) -> Path:
        source = Path(source_audio).expanduser().resolve()
        if not source.is_file():
            raise InvalidVoiceProfileError(f"reference audio does not exist: {source}")
        try:
            with wave.open(str(source), "rb") as audio:
                valid = (
                    audio.getnchannels() == 1
                    and audio.getframerate() == 24000
                    and audio.getsampwidth() == 2
                )
        except (wave.Error, EOFError) as exc:
            raise InvalidVoiceProfileError("reference audio must be a 24kHz mono WAV") from exc
        if not valid:
            raise InvalidVoiceProfileError("reference audio must be a 24kHz mono WAV")
        return source

    def create_profile(
        self,
        profile_id: str,
        display_name: str,
        reference_text: str,
        source_audio: str | Path,
        default_instruction: str = "",
    ) -> VoiceProfile:
        profile_dir = self._profile_dir(profile_id)
        if profile_dir.exists():
            raise InvalidVoiceProfileError(f"profile already exists: {profile_id}")
        if not reference_text or not reference_text.strip():
            raise InvalidVoiceProfileError("reference_text must not be empty")
        source = self.validate_reference_audio(source_audio)

        profile_dir.mkdir(parents=True, exist_ok=False)
        reference_path = profile_dir / "reference.wav"
        shutil.copyfile(source, reference_path)
        checksum = hashlib.sha256(reference_path.read_bytes()).hexdigest()
        profile = VoiceProfile(
            profile_id=profile_id,
            display_name=display_name,
            reference_audio="reference.wav",
            reference_text=reference_text.strip(),
            default_instruction=default_instruction.strip(),
            checksum_sha256=checksum,
        )
        (profile_dir / "profile.json").write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (profile_dir / "checksum.sha256").write_text(checksum + "\n", encoding="ascii")
        return profile

    def get_profile(self, profile_id: str) -> VoiceProfile:
        profile_dir = self._profile_dir(profile_id)
        metadata_path = profile_dir / "profile.json"
        if not metadata_path.is_file():
            raise InvalidVoiceProfileError(f"profile not found: {profile_id}")
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            profile = VoiceProfile.from_dict(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise InvalidVoiceProfileError(f"invalid profile metadata: {profile_id}") from exc
        reference_path = profile_dir / profile.reference_audio
        self.validate_reference_audio(reference_path)
        return profile

    def list_profiles(self) -> list[VoiceProfile]:
        if not self.root.is_dir():
            return []
        profiles: list[VoiceProfile] = []
        for profile_dir in sorted(self.root.iterdir(), key=lambda item: item.name.lower()):
            if not profile_dir.is_dir() or not (profile_dir / "profile.json").is_file():
                continue
            profiles.append(self.get_profile(profile_dir.name))
        return profiles
