from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar


def _require_text(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _require_non_negative(value: float, field_name: str) -> float:
    number = float(value)
    if number < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return number


@dataclass(frozen=True)
class NarrationBlock:
    block_id: str
    spoken_text: str
    display_text: str
    pause_after_ms: int = 250

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_id", _require_text(self.block_id, "block_id"))
        object.__setattr__(self, "spoken_text", _require_text(self.spoken_text, "spoken_text"))
        object.__setattr__(self, "display_text", _require_text(self.display_text, "display_text"))
        if self.pause_after_ms < 0:
            raise ValueError("pause_after_ms must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "spoken_text": self.spoken_text,
            "display_text": self.display_text,
            "pause_after_ms": self.pause_after_ms,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NarrationBlock":
        return cls(**payload)


@dataclass(frozen=True)
class AlignmentCharacter:
    char: str
    index: int
    start: float
    end: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "char", _require_text(self.char, "char"))
        if self.index < 0:
            raise ValueError("index must be non-negative")
        start = _require_non_negative(self.start, "start")
        end = _require_non_negative(self.end, "end")
        if end <= start:
            raise ValueError("end must be greater than start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def to_dict(self) -> dict[str, Any]:
        return {"char": self.char, "index": self.index, "start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AlignmentCharacter":
        return cls(**payload)


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        if self.index <= 0:
            raise ValueError("index must be positive")
        start = _require_non_negative(self.start, "start")
        end = _require_non_negative(self.end, "end")
        if end <= start:
            raise ValueError("end must be greater than start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "text", _require_text(self.text, "text"))

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "start": self.start, "end": self.end, "text": self.text}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SubtitleCue":
        return cls(**payload)


@dataclass(frozen=True)
class VoiceProfile:
    profile_id: str
    display_name: str
    reference_audio: str
    reference_text: str
    default_instruction: str = ""
    checksum_sha256: str = ""

    def __post_init__(self) -> None:
        for field_name in ("profile_id", "display_name", "reference_audio", "reference_text"):
            object.__setattr__(self, field_name, _require_text(getattr(self, field_name), field_name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "reference_audio": self.reference_audio,
            "reference_text": self.reference_text,
            "default_instruction": self.default_instruction,
            "checksum_sha256": self.checksum_sha256,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VoiceProfile":
        return cls(**payload)


@dataclass(frozen=True)
class PipelineManifest:
    task_id: str
    stages: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    alignment_degraded: bool = False

    VALID_STAGES: ClassVar[frozenset[str]] = frozenset(
        {"pending", "running", "completed", "failed", "degraded"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_text(self.task_id, "task_id"))
        for name, stage in self.stages.items():
            if stage not in self.VALID_STAGES:
                raise ValueError(f"unsupported manifest stage: {name}={stage}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "stages": dict(self.stages),
            "artifacts": dict(self.artifacts),
            "alignment_degraded": self.alignment_degraded,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PipelineManifest":
        return cls(**payload)
